#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""version_guard — 打包／推送前檢查 ``VERSION`` 有無跟住程式碼升。

### 為何需要（實測踩過）
``VERSION`` 係人手改的檔。2026-11-02 實測發生過一次：另一個對話改好功能並
commit（``3ea0fa1`` 快捷鍵修正），但**冇升 VERSION**，直接打包就會出
「zip 叫 1.0.0、內容已變、程式自報 1.0.0」——同事回報問題時，你問「跑邊版」
他答 1.0.0，你分唔清係修之前定修之後。

### 判斷邏輯（只用 git 事實，唔用時間／commit 數）
1. 搵「最後一次改動 ``VERSION`` 的 commit」做基線
2. 睇基線之後有冇 commit 改過**程式碼**
3. 有 → 警告（``--strict`` 時失敗）；只改 docs／md／.gitignore 就當唔算

### 設計取捨
- **預設只警告，唔擋**：docs-only 改動、hotfix 試跑唔應該被鎖死。
  要硬性把關就加 ``--strict``，或將 pre-push hook 最後一行 ``exit 0`` 改
  ``exit $?``。
- **唔用 mtime 或「幾耐之前」**：換電腦、clone 新 repo 都會令時間判斷誤報。
- **唔係 git 環境就靜靜跳過**：同事手上的交付包冇 ``.git``，唔應該噴錯。
- 本模組**只讀 git，唔改任何檔**，所以可以安全咁被 ``build_release.py``
  與 pre-push hook 共用。

用法::

    python tools/version_guard.py            # 只警告（exit 0）
    python tools/version_guard.py --strict   # 有未升版就 exit 1
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

#: 專案根目錄（``tools/`` 的上一層）
ROOT = Path(__file__).resolve().parent.parent

#: 只改這些路徑的 commit **唔算**「需要升版」
IGNORED_PREFIXES: tuple[str, ...] = ("docs/", "_probe/", "_smoke/", ".workbuddy/", "dist/")

#: 只改這些副檔名（任何位置）都唔算
IGNORED_SUFFIXES: tuple[str, ...] = (".md",)

#: 這些檔名本身唔算（VERSION 當然要排除，否則自己豁免自己）
IGNORED_NAMES: frozenset[str] = frozenset({"VERSION", ".gitignore", ".gitattributes"})

#: 警告時最多列出幾個 commit（免得刷屏）
MAX_LISTED = 5


def _git(args: Sequence[str], cwd: Path) -> str | None:
    """執行 git 並回傳 stdout。

    Args:
        args: git 子命令與參數（不含 ``git`` 本身）。
        cwd: 執行目錄。

    Returns:
        stdout 字串；git 不存在、非 repo 或命令失敗時回傳 ``None``
        （刻意唔拋例外 —— 檢查器壞掉不應該令打包停摆）。
    """
    try:
        proc = subprocess.run(
            # core.quotePath=false 必須：否則 git 會把非 ASCII 路徑輸出成
            # "docs/\346\236..." 這類 C 式轉義（連引號），令 docs/ 前綴比對失效。
            # 本專案的 docs 檔名全中文，冇這個設定會誤報「改過程式碼」。
            ["git", "-c", "core.quotePath=false", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_git_repo(root: Path = ROOT) -> bool:
    """判斷目錄是否在 git 工作區內。

    Args:
        root: 專案根目錄。

    Returns:
        ``True`` 表示可用 git 查歷史。
    """
    out = _git(["rev-parse", "--is-inside-work-tree"], root)
    return bool(out and out.strip() == "true")


def read_version(root: Path = ROOT) -> str:
    """讀取 ``VERSION`` 內容。

    Args:
        root: 專案根目錄。

    Returns:
        版本字串；讀唔到回傳 ``"?"``（本模組只負責提示，唔應該因此報錯）。
    """
    try:
        text = (root / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"
    return text.splitlines()[0].strip() if text else "?"


def last_version_commit(root: Path = ROOT) -> str | None:
    """找最後一次改動 ``VERSION`` 的 commit。

    Args:
        root: 專案根目錄。

    Returns:
        完整 commit hash；``VERSION`` 從未被 commit（或 git 不可用）時回傳 ``None``。
    """
    out = _git(["log", "-1", "--format=%H", "--", "VERSION"], root)
    if not out:
        return None
    commit = out.strip()
    return commit or None


def commits_since(root: Path, rev: str) -> list[tuple[str, str]]:
    """列出 ``rev`` 之後的 commit（最新在前）。

    Args:
        root: 專案根目錄。
        rev: 基線 commit hash。

    Returns:
        ``(短 hash, 標題)`` 清單；git 不可用時回傳空清單。
    """
    out = _git(["log", "--format=%h%x09%s", f"{rev}..HEAD"], root)
    if not out:
        return []
    rows: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        short, _, subject = line.partition("\t")
        rows.append((short, subject))
    return rows


def _is_ignored(rel: str) -> bool:
    """判斷單一改動路徑是否屬於「唔需要升版」的類別。

    Args:
        rel: 相對於 repo 根的路徑（git 用 forward slash）。

    Returns:
        ``True`` 表示這條路徑的改動唔計入「改過程式碼」。
    """
    # 即使已用 core.quotePath=false，仍清一次引號（舊版 git 或 -z 混用時保險）
    path = rel.replace("\\", "/").strip().strip('"')
    if path in IGNORED_NAMES:
        return True
    if path.startswith(IGNORED_PREFIXES):
        return True
    return path.lower().endswith(IGNORED_SUFFIXES)


def code_changes_since(root: Path, rev: str) -> list[str]:
    """列出 ``rev`` 之後有改動的「非文件」檔案。

    Args:
        root: 專案根目錄。
        rev: 基線 commit hash。

    Returns:
        路徑清單（已過濾 docs／md／VERSION／忽略目錄）。空清單代表只需
        要升版的實質改動都冇。
    """
    out = _git(["diff", "--name-only", f"{rev}..HEAD"], root)
    if not out:
        return []
    files = [line.strip() for line in out.splitlines() if line.strip()]
    return [f for f in files if not _is_ignored(f)]


def check_version_bump(root: Path = ROOT, *, strict: bool = False, echo: bool = True) -> int:
    """檢查 ``VERSION`` 是否跟得上程式碼改動。

    Args:
        root: 專案根目錄。
        strict: ``True`` 時發現未升版會回傳 1（預設只警告，回傳 0）。
        echo: 是否印出結果（測試時可關掉）。

    Returns:
        0 = 通過或只是警告；1 = ``strict`` 模式下發現未升版。

    Raises:
        無。所有 git 失敗都退化為「跳過檢查」，避免阻礙正常流程。
    """

    def say(msg: str) -> None:
        if echo:
            print(msg)

    if not is_git_repo(root):
        say("[version] 非 git 環境，跳過 VERSION 檢查（交付包內屬正常）")
        return 0

    version = read_version(root)
    baseline = last_version_commit(root)

    if baseline is None:
        # VERSION 從未被 commit：只在「已有 commit」時才有意義
        history = _git(["rev-list", "--count", "HEAD"], root)
        if history and history.strip() not in ("", "0"):
            say(f"[version] ⚠ VERSION 從未被 commit（現值 {version}），"
                "請確認是否要一併提交")
        return 0

    pending = commits_since(root, baseline)
    if not pending:
        say(f"[version] OK：VERSION {version} 對應最新 commit")
        return 0

    changes = code_changes_since(root, baseline)
    if not changes:
        say(f"[version] OK：VERSION {version} 之後只有文件改動（{len(pending)} 個 commit），不需升版")
        return 0

    say(f"[version] ⚠ VERSION 仍係 {version}，但之後有 {len(pending)} 個 commit 改過程式碼：")
    for short, subject in pending[:MAX_LISTED]:
        say(f"           {short}  {subject}")
    if len(pending) > MAX_LISTED:
        say(f"           …另有 {len(pending) - MAX_LISTED} 個")
    preview = ", ".join(changes[:6])
    more = f" 等 {len(changes)} 個檔" if len(changes) > 6 else ""
    say(f"           改動檔案：{preview}{more}")
    say("           → 要出包就先改 VERSION（例如 1.0.1 → 1.0.2），否則 zip 與程式自報版本會對唔上。")

    if strict:
        say("[version] ✕ --strict：請先升 VERSION 再打包")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """命令列入口。

    Args:
        argv: 參數清單（預設取 ``sys.argv[1:]``）。

    Returns:
        進程退出碼：0 通過／警告；1 ``--strict`` 且有未升版改動。
    """
    parser = argparse.ArgumentParser(
        prog="version_guard.py",
        description="檢查 VERSION 有無跟住程式碼升（預設只警告，--strict 才擋）",
    )
    parser.add_argument("--strict", action="store_true",
                        help="發現未升版時回傳 exit 1（否則只警告）")
    args = parser.parse_args(argv)
    return check_version_bump(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
