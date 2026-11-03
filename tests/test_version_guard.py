# -*- coding: utf-8 -*-
"""test_version_guard — 版本護欄邏輯驗證（無 Tk、無網絡，直接跑）。

用法：.venv-ui/Scripts/python.exe tests/test_version_guard.py
（或 .venv/Scripts/python.exe，只需要 stdlib + git）

每個 case 都開一個獨立臨時 git repo，所以唔會影響本專案的 git 狀態。
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.version_guard import check_version_bump, commits_with_code_changes  # noqa: E402

PASS: list[tuple[str, bool]] = []


def check(name: str, cond: bool) -> None:
    """記錄一條斷言結果。

    Args:
        name: 檢查項目名稱。
        cond: 斷言條件。
    """
    PASS.append((name, cond))
    print(("✅ " if cond else "❌ ") + name)


def _git(repo: Path, *args: str) -> None:
    """在指定 repo 執行 git（失敗即拋，方便定位）。

    Args:
        repo: repo 目錄。
        *args: git 參數。

    Raises:
        subprocess.CalledProcessError: git 指令失敗。
    """
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True, encoding="utf-8", errors="replace")


def _git_out(repo: Path, *args: str) -> str:
    """在指定 repo 執行 git 並取回 stdout。

    Args:
        repo: repo 目錄。
        *args: git 參數。

    Returns:
        stdout 字串（失敗時回傳空字串，方便斷言）。
    """
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.stdout if proc.returncode == 0 else ""


def _subdir(parent: Path, name: str) -> Path:
    """建立（並回傳）一個子目錄，供 :func:`make_repo` 建立第二個 repo。

    Args:
        parent: 父目錄。
        name: 子目錄名稱。

    Returns:
        已建立的子目錄路徑。
    """
    path = parent / name
    path.mkdir(parents=True)
    return path


def _commit(repo: Path, message: str, files: dict[str, str]) -> None:
    """寫入檔案並 commit。

    Args:
        repo: repo 目錄。
        message: commit 訊息。
        files: 相對路徑 → 內容。
    """
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def make_repo(tmp: Path) -> Path:
    """建立一個已 commit VERSION + app.py 的臨時 repo。

    Args:
        tmp: 臨時根目錄。

    Returns:
        新 repo 的路徑。
    """
    repo = tmp / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    _commit(repo, "init", {"VERSION": "1.0.0\n", "app.py": "print(1)\n"})
    return repo


def main() -> int:
    """跑全部 case。

    Returns:
        0 全部通過，1 有失敗。
    """
    tmp = Path(tempfile.mkdtemp(prefix="karaoke_vguard_"))
    try:
        repo = make_repo(tmp)

        check("基線：VERSION 對應最新 commit → 通過",
              check_version_bump(repo, echo=False) == 0)

        _commit(repo, "feat: 改 code 但冇升版", {"app.py": "print(2)\n"})
        check("未升版：預設只警告（rc=0）",
              check_version_bump(repo, echo=False) == 0)
        check("未升版：--strict 擋下（rc=1）",
              check_version_bump(repo, strict=True, echo=False) == 1)

        _commit(repo, "chore: bump 1.0.1", {"VERSION": "1.0.1\n"})
        check("升版後：strict 亦通過",
              check_version_bump(repo, strict=True, echo=False) == 0)

        _commit(repo, "docs: 改 README", {"README.md": "hi\n"})
        check("只改根目錄 .md → 唔算需要升版",
              check_version_bump(repo, strict=True, echo=False) == 0)

        _commit(repo, "docs: 加 ADR", {"docs/架構決策紀錄.md": "x\n"})
        check("只改 docs/ 內檔案 → 唔算",
              check_version_bump(repo, strict=True, echo=False) == 0)

        _commit(repo, "chore: 改 lock", {"requirements.lock.txt": "x==1\n"})
        check("改 requirements.lock.txt → 算（交付內容變了）",
              check_version_bump(repo, strict=True, echo=False) == 1)

        plain = tmp / "plain"
        plain.mkdir()
        (plain / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        check("非 git 環境 → 靜靜跳過（rc=0）",
              check_version_bump(plain, strict=True, echo=False) == 0)

        # --- 回歸測試：警告只應列出「真正改過程式碼」的 commit -------------
        # 背景：v1.0.2 時只改 requirements-ui.txt 一個註解，卻報「9 個 commit
        # 改過程式碼」，其中 8 個是純文件 commit。判定正確但訊息誤導。
        mixed = make_repo(_subdir(tmp, "mixed"))
        _commit(mixed, "docs: 改 README", {"README.md": "hi\n"})
        _commit(mixed, "docs: 加 ADR", {"docs/架構決策紀錄.md": "y\n"})
        _commit(mixed, "feat: 改 code", {"app.py": "print(9)\n"})

        baseline = _git_out(mixed, "log", "--format=%H", "--", "VERSION").strip()
        listed = commits_with_code_changes(mixed, baseline)
        check("混合 commit：只列 1 個改過程式碼的 commit", len(listed) == 1)
        check("混合 commit：列出的是 feat 那個",
              bool(listed) and listed[0][1] == "feat: 改 code")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            check_version_bump(mixed, echo=True)
        out = buf.getvalue()
        check("警告文字：計數用 filtered 數量（1 個，非 3 個）",
              "1 個 commit 改過程式碼" in out)
        check("警告文字：不列出純文件 commit",
              "docs: 改 README" not in out and "docs: 加 ADR" not in out)
        check("警告文字：仍列出真正動 code 的 commit", "feat: 改 code" in out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [name for name, ok in PASS if not ok]
    print()
    if failed:
        print(f"FAILED ({len(failed)}/{len(PASS)} checks) → {failed}")
        return 1
    print(f"ALL PASS ({len(PASS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
