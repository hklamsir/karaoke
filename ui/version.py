# -*- coding: utf-8 -*-
"""version — 版本號讀取（唯一真源 = 專案根目錄的 ``VERSION`` 檔）。

為何唔喺 ``ui/__init__.py`` 硬編碼字串：本專案用 **zip 覆蓋式更新**（見
``DEPLOY.md`` §6），版本號必須與交付包一致；``VERSION`` 同時係打包腳本
``tools/build_release.py`` 讀的同一個檔，避免「UI 標題寫 1.0.0、zip 叫
1.0.1」這種對不上的情況。

CLI（``main.py``）刻意**唔** import 本模組 —— 命令行工具唔應該為讀一個版本號
而拉入整個 UI 套件；該處有一個同樣只讀 ``VERSION`` 的本地函式（5 行）。
兩者共用同一個資料檔，所以唔存在數字不同步問題。
"""

from __future__ import annotations

from pathlib import Path

#: 專案根目錄（``ui/`` 的上一層）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 版本檔位置
VERSION_PATH = PROJECT_ROOT / "VERSION"

#: 讀唔到 ``VERSION`` 時的後備值（例如有人只複製幾個 .py 出來跑）
FALLBACK = "dev"

#: 預設視窗標題（不含版本）
BASE_TITLE = "Karaoke AI Subtitler - Designed by Lamsir"


def read_version(path: Path | None = None) -> str:
    """讀取版本號。

    Args:
        path: 自訂 ``VERSION`` 路徑（測試用）；預設用專案根目錄那份。

    Returns:
        版本字串，例如 ``"1.0.0"``。檔案不存在、讀取失敗或內容為空時，
        回傳 :data:`FALLBACK`（``"dev"``），**唔會拋例外** —— 版本號讀唔到
        不應該令程式開唔到。
    """
    target = VERSION_PATH if path is None else path
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return FALLBACK
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    return first or FALLBACK


def window_title(base: str = BASE_TITLE) -> str:
    """回傳含版本號的視窗標題。

    Args:
        base: 標題主體，預設 :data:`BASE_TITLE`。

    Returns:
        ``"<base>  (v1.0.0)"``；版本為 ``dev`` 時原樣回傳 ``base``，避免出現
        「(vdev)」這種冇意義的標題。
    """
    current = read_version()
    if current == FALLBACK:
        return base
    return f"{base}  (v{current})"
