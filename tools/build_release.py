#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_release — 打包交付 zip（allowlist 方式）。

為何唔用「整個資料夾壓縮」：本 repo 混住 ``_probe/``（127 張測試截圖）、
``_smoke/``（60MB 實測素材）、``.venv*``（可重建）、``docs/*計劃書.md``
（內部文件）同 ``.workbuddy/``（Agent 記憶）。整包壓會將這些全部交給同事，
既混淆視聽又可能外洩內部文件。所以此處用**顯式白名單**，新增檔案時要
明確加入 ``TOP_FILES`` 或 ``DIR_PATTERNS``。

打包後會做三項自檢（任何一項失敗即中止，唔會留低半成品 zip）：

1. **必要檔案齊全** —— ``main.py`` / ``run_ui.bat`` / ``requirements.lock.txt`` 等
2. **冇夾帶禁區** —— ``_probe`` / ``.venv`` / ``__pycache__`` / 計劃書 / ``.env``
3. **冇 API key 洩漏** —— 掃描所有文字檔有無 ``sk-`` 加 20 位以上字元

打包前另會先跑 ``tools/version_guard.py``：若 ``VERSION`` 之後仍有改過程式碼
的 commit，會出警告（``--strict`` 則直接中止）。理由見該模組 docstring。

用法::

    python tools/build_release.py              # 依 VERSION 出 dist/karaoke-v1.0.0.zip
    python tools/build_release.py --dry-run    # 只印清單，唔寫檔
    python tools/build_release.py --out D:/tmp # 自訂輸出資料夾
    python tools/build_release.py --strict     # VERSION 未升就唔准出包

版本號唯一真源係專案根目錄的 ``VERSION``（同 ``ui/app.py`` 標題、``main.py
--version`` 一致）。要出新版就改該檔再跑本腳本，刻意唔提供 ``--version``
參數，避免出現「zip 叫 1.1.0、程式自報 1.0.0」。
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterator, Sequence

#: 專案根目錄（``tools/`` 的上一層）
ROOT = Path(__file__).resolve().parent.parent

# 版本護欄與本腳本同住 tools/：直接執行時 sys.path[0] 係 tools/，
# 但被當成 `tools.build_release` import 時就要用完整路徑 —— 兩種都支援。
try:
    from tools.version_guard import check_version_bump
except ImportError:   # 直接執行 tools/build_release.py 的情況
    from version_guard import check_version_bump

#: 版本檔（唯一真源）
VERSION_FILE = ROOT / "VERSION"

#: 放在根目錄的單一檔案（必須全部存在）
TOP_FILES: tuple[str, ...] = (
    "main.py",
    "run_ui.bat",
    "run_tests.bat",
    "VERSION",
    "README.md",
    "DEPLOY.md",
    "requirements.txt",
    "requirements-ui.txt",
    "requirements.lock.txt",
    # 交付包內含 tests/，所以要一併收「跑得動測試」的三件配套：
    # 缺 pytest.ini 會噴 unknown marker 警告、缺 requirements-dev.txt 無從裝 pytest、
    # 缺 run_tests.bat 就要人手記指令。三者缺一，包內的測試等於不可用。
    "requirements-dev.txt",
    "pytest.ini",
)

#: 資料夾 → 收錄樣式（相對該資料夾；``**/*`` 代表全部，但仍會過濾禁區）
DIR_PATTERNS: dict[str, tuple[str, ...]] = {
    "ui": ("**/*.py",),
    "assets": ("**/*",),
    "tests": ("**/*.py", "**/*.json"),
    "tools": ("**/*.py",),
    "presets": ("**/*.json",),
    # 內部文件（計劃書、線框圖）刻意唔外發，所以只列三個檔名
    "docs": ("使用說明.md", "名詞表.md", "架構決策紀錄.md"),
}

#: 任何層級出現即跳過
EXCLUDE_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".venv", ".venv-ui", "_probe", "_smoke", ".workbuddy", ".git", "dist"}
)

#: 任何層級出現即跳過
EXCLUDE_NAMES: frozenset[str] = frozenset(
    {".DS_Store", "Thumbs.db", ".env", "settings.json", "desktop.ini"}
)

#: 副檔名過濾（編譯產物）
EXCLUDE_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo"})

#: 打包後必須存在的項目（相對 zip 內前綴之後的路徑）
REQUIRED_ENTRIES: tuple[str, ...] = (
    "main.py",
    "run_ui.bat",
    "VERSION",
    "requirements.lock.txt",
    "DEPLOY.md",
    "ui/__main__.py",
    "ui/version.py",
    "assets/app.ico",
    "presets/home_style.json",
    "docs/使用說明.md",
)

#: zip 內出現即視為打包錯誤（漏了過濾）
FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "_probe/",
    "_smoke/",
    ".venv",
    ".workbuddy/",
    "__pycache__/",
    "計劃書",
    ".env",
    "settings.json",
)

#: API key 樣式（DashScope key 以 sk- 開頭；要求 20 位以上避免誤中 README 的 sk-xxxx）
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")

#: 掃描機密時視為文字的副檔名
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".md", ".txt", ".json", ".bat", ".toml", ".yml", ".yaml", ""}
)


def read_version(path: Path = VERSION_FILE) -> str:
    """讀取版本號。

    Args:
        path: ``VERSION`` 檔路徑。

    Returns:
        版本字串，例如 ``"1.0.0"``。

    Raises:
        SystemExit: 檔案不存在或內容為空（打包唔應該靠後備值）。
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        sys.exit(f"[build] 讀唔到 {path}：{exc}")
    first = text.splitlines()[0].strip() if text else ""
    if not first:
        sys.exit(f"[build] {path} 係空檔，請先寫入版本號（例如 1.0.0）")
    return first


def iter_allowlisted() -> Iterator[Path]:
    """逐一產生要打包的檔案（已過濾禁區）。

    Yields:
        專案內的檔案路徑（絕對路徑），順序穩定（根目錄檔案先，再按資料夾）。
    """
    for name in TOP_FILES:
        p = ROOT / name
        if p.is_file():
            yield p
        else:
            print(f"[build] ⚠ 缺少根目錄檔案：{name}")

    for folder, patterns in DIR_PATTERNS.items():
        base = ROOT / folder
        if not base.is_dir():
            print(f"[build] ⚠ 缺少資料夾：{folder}/")
            continue
        found: set[Path] = set()
        for pattern in patterns:
            found.update(p for p in base.glob(pattern) if p.is_file())
        for p in sorted(found):
            yield p


def is_included(path: Path) -> bool:
    """判斷單一檔案是否應該入包。

    Args:
        path: 檔案路徑（必須位於 :data:`ROOT` 之下）。

    Returns:
        ``True`` 表示可入包。禁區資料夾、編譯產物、機密檔名一律 ``False``。
    """
    try:
        rel_parts = path.relative_to(ROOT).parts
    except ValueError:
        return False
    if any(part in EXCLUDE_DIRS for part in rel_parts[:-1]):
        return False
    if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
        return False
    return path.is_file()


def collect() -> list[Path]:
    """收集最終入包清單（去重 + 過濾 + 排序）。

    Returns:
        相對於 :data:`ROOT` 的檔案路徑清單，按字串排序以確保打包結果穩定。
    """
    files = {p for p in iter_allowlisted() if is_included(p)}
    return sorted(files, key=lambda p: p.relative_to(ROOT).as_posix().lower())


def scan_secrets(files: Sequence[Path]) -> list[tuple[Path, str]]:
    """掃描文字檔有無疑似 API key。

    Args:
        files: 入包檔案清單。

    Returns:
        ``(檔案, 命中片段首 12 字元)`` 清單；空清單代表乾淨。命中片段會截短，
        避免將 key 本身印到 console 或 CI log。
    """
    hits: list[tuple[Path, str]] = []
    for p in files:
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if p.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = SECRET_PATTERN.search(text)
        if match:
            hits.append((p, match.group(0)[:12] + "…"))
    return hits


def build(version: str, out_dir: Path, *, dry_run: bool = False,
          strict: bool = False) -> Path | None:
    """生成交付 zip。

    Args:
        version: 版本字串（決定 zip 檔名與內部根目錄名）。
        out_dir: 輸出資料夾（不存在會建立）。
        dry_run: ``True`` 時只印清單，唔寫檔。
        strict: ``True`` 時「VERSION 未跟住程式碼升」會中止打包；預設只警告。

    Returns:
        產出的 zip 路徑；``dry_run`` 時回傳 ``None``。

    Raises:
        SystemExit: 自檢失敗（缺檔、夾帶禁區、發現金鑰、strict 下未升版）。
    """
    # 先驗版本號有無跟住 code 升 —— 否則會出「zip 與程式自報版本對唔上」的包
    if check_version_bump(strict=strict) != 0:
        sys.exit("[build] ✕ VERSION 未跟住程式碼升（詳見上），已中止打包")

    files = collect()
    prefix = f"karaoke-v{version}"
    rels = [p.relative_to(ROOT).as_posix() for p in files]

    print(f"[build] 版本 {version}，{len(files)} 個檔案")
    for rel in rels:
        print(f"        {rel}")

    # ---- 自檢 1：必要檔案
    missing = [name for name in REQUIRED_ENTRIES if name not in rels]
    if missing:
        sys.exit(f"[build] ✕ 缺少必要檔案：{', '.join(missing)}")

    # ---- 自檢 2：禁區
    leaked = [r for r in rels if any(bad in f"/{r}" for bad in FORBIDDEN_SUBSTRINGS)]
    if leaked:
        sys.exit("[build] ✕ 清單夾帶禁區檔案：\n        " + "\n        ".join(leaked))

    # ---- 自檢 3：金鑰
    hits = scan_secrets(files)
    if hits:
        sys.exit(
            "[build] ✕ 疑似 API key：\n        "
            + "\n        ".join(f"{p.relative_to(ROOT).as_posix()} → {frag}" for p, frag in hits)
            + "\n        請先將該值改為環境變數或佔位符再打包。"
        )

    total = sum(p.stat().st_size for p in files)
    if dry_run:
        print(f"[build] dry-run：未寫檔（原始大小 {total / 1024 / 1024:.2f} MB）")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{prefix}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            zf.write(p, arcname=f"{prefix}/{p.relative_to(ROOT).as_posix()}")

    size = zip_path.stat().st_size
    print(
        f"[build] ✓ {zip_path}\n"
        f"        檔案 {len(files)} 個｜原始 {total / 1024 / 1024:.2f} MB｜"
        f"壓縮後 {size / 1024 / 1024:.2f} MB"
    )
    return zip_path


def main(argv: Sequence[str] | None = None) -> int:
    """命令列入口。

    Args:
        argv: 參數清單（預設取 ``sys.argv[1:]``）。

    Returns:
        進程退出碼：0 成功，1 失敗。
    """
    parser = argparse.ArgumentParser(
        prog="build_release.py",
        description="依 VERSION 打包交付 zip（allowlist，會自檢禁區與 API key）",
    )
    parser.add_argument("--out", default=str(ROOT / "dist"),
                        help="輸出資料夾（預設 dist/）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印清單與自檢結果，唔寫檔")
    parser.add_argument("--strict", action="store_true",
                        help="VERSION 未跟住程式碼升就中止（預設只警告）")
    args = parser.parse_args(argv)

    version = read_version()
    build(version, Path(args.out), dry_run=args.dry_run, strict=args.strict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
