# -*- coding: utf-8 -*-
"""``python -m ui`` 啟動入口。

⚠️ R24：UI 要用有 tkinter 嘅 venv —— ``.venv-ui``（由 ``py -3`` 建）。
CLI 嘅 ``.venv`` 冇 Tcl/Tk，用錯會喺度攔截並提示正確命令。
"""

from __future__ import annotations

import sys


def _fatal_no_tkinter() -> int:
    print("✕ 呢個 venv 冇 tkinter（ModuleNotFoundError: No module named 'tkinter'）", file=sys.stderr)
    print("", file=sys.stderr)
    print("字幕編輯器 UI 要用 .venv-ui（見 ADR-014 R24）：", file=sys.stderr)
    print("", file=sys.stderr)
    print("  首次安裝：", file=sys.stderr)
    print("    py -3 -m venv .venv-ui", file=sys.stderr)
    print("    .venv-ui\\Scripts\\python.exe -m pip install -r requirements-ui.txt", file=sys.stderr)
    print("", file=sys.stderr)
    print("  啟動 UI：", file=sys.stderr)
    print("    .venv-ui\\Scripts\\python.exe -m ui", file=sys.stderr)
    print("", file=sys.stderr)
    print("  （或者直接雙擊 / 執行 run_ui.bat）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        from .app import run
    except ModuleNotFoundError as exc:
        if "tkinter" in str(exc):
            sys.exit(_fatal_no_tkinter())
        raise
    sys.exit(run())
