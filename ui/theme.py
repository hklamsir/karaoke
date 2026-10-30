# -*- coding: utf-8 -*-
"""theme — ttkbootstrap 主題 + 自訂色票（計劃書 §4.1）+ 字型（§4.2）+ 間距（§4.3）。

色票原則（D1 決定）：深色底（#1e1e26 / #2b2b36）+ 青藍 accent #3AA0FF。
Accent 刻意**唔用黃色** —— 專案字幕「唱過色」係 #FFFF00，介面 accent 同佢撞色
會令用戶分唔清邊個係字幕色、邊個係介面色。
"""

from __future__ import annotations

import tkinter.font as tkfont

# ---------------------------------------------------------------------------
# 色票（§4.1）
# ---------------------------------------------------------------------------

BG_DEEP = "#16161d"        # 主視窗底（最深）
BG_PANEL = "#1e1e26"       # sidebar / 卡片
BG_HOVER = "#2b2b36"       # 按鈕 hover、輸入框 focus
BORDER = "#3a3a48"         # 分隔線、Treeview 格線
FG_MAIN = "#e6e6ec"        # 文字（主）
FG_DIM = "#a8a8bc"         # 文字（次）—— 實測 #8a8a9c 喺深色底太難看清（用戶回報），調亮
FG_DIMMER = "#7a7a8c"      # 文字（裝飾性極次：tick 標籤等，唔重要先好用）
ACCENT = "#3AA0FF"         # 主按鈕、focus ring、選取行、游標線
ACCENT_DIM = "#1E6FBF"     # accent pressed
SUCCESS = "#3DD68C"        # 完成、已驗證
WARNING = "#F5A623"        # 「估算字」標記
DANGER = "#FF5C5C"         # 刪除、錯誤

#: 字幕「唱過 / 未唱」預設色 —— **保留專案原值**，同 timings.json 一致
SUB_PRIMARY = "#FFFF00"
SUB_SECONDARY = "#FFFFFF"

# ---------------------------------------------------------------------------
# 字型（§4.2）
# ---------------------------------------------------------------------------

FONT_UI = "Microsoft JhengHei UI"       # 介面（中文）
FONT_UI_LATIN = "Segoe UI"              # 介面（西文 fallback 用）
FONT_MONO = "Cascadia Mono"             # 數值（ms / 時間碼）—— 等寬防跳（功能需求）

_FONT_NAMES_CACHE: list[str] | None = None


def available_font(names: list[str]) -> str:
    """喺 ``names`` 入面揀第一個系統有嘅字型，冇就返最尾一個（fallback）。"""
    global _FONT_NAMES_CACHE
    if _FONT_NAMES_CACHE is None:
        try:
            root = tkfont._get_default_root()   # noqa: SLF001
            _FONT_NAMES_CACHE = set(tkfont.families(root))
        except Exception:   # noqa: BLE001 — 冇 root 時（測試環境）直接用第一個
            return names[-1]
    lowered = {n.lower() for n in _FONT_NAMES_CACHE}
    for name in names:
        if name.lower() in lowered:
            return name
    return names[-1]


def mono_font_family() -> str:
    """等寬字型 cascade：Cascadia Mono → Cascadia Code → Consolas → monospace。"""
    return available_font([FONT_MONO, "Cascadia Code", "Consolas", "TkFixedFont"])


def ui_font_family() -> str:
    """介面字型 cascade：JhengHei UI → JhengHei → Noto Sans CJK TC → Segoe UI。"""
    return available_font(
        [FONT_UI, "Microsoft JhengHei", "Noto Sans CJK TC", FONT_UI_LATIN])


# ---------------------------------------------------------------------------
# 間距 / 尺寸（§4.3）
# ---------------------------------------------------------------------------

SPACE = 8            # 基礎間距單位（所有 padding 用 4/8/12/16/24）
SIDEBAR_W = 200      # sidebar 寬（可拖曳 160~280）
TOOLBAR_H = 48       # 工具列高
STATUSBAR_H = 28     # 狀態列高
ROW_H = 28           # Treeview 行高
CHAR_MIN_W = 3       # 時間軸最小 char 寬度（細過就聚合顯示）


def apply_ttkbootstrap_theme(root) -> str:
    """套用 darkly 主題 + 微調色票（輸入框背景 / Treeview 行高等）。

    R1 對策：ttkbootstrap 同 Python 3.13 相容性喺 app 啟動時驗證；
    import / style 失敗會拋例外畀 caller 降級。

    Returns:
        實際套用嘅主題名。
    """
    import ttkbootstrap as ttkb

    style = ttkb.Style(theme="darkly")
    # 統一輸入框 / 下拉框底色（darkly 預設偏淺，對齊 §4.1 色票）
    for element in ("TEntry", "TCombobox", "TSpinbox"):
        try:
            style.configure(element, fieldbackground=BG_PANEL,
                            background=BG_HOVER, foreground=FG_MAIN,
                            bordercolor=BORDER, lightcolor=BG_PANEL, darkcolor=BG_PANEL)
        except Exception:   # noqa: BLE001 — 個別 element 唔存在就跳過
            pass
    # Treeview：行高 28 + 深色底 + 選取行 accent
    style.configure("Treeview", rowheight=28, background=BG_PANEL, fieldbackground=BG_PANEL,
                    foreground=FG_MAIN, bordercolor=BORDER)
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#000000")])
    style.configure("Treeview.Heading", background=BG_HOVER, foreground=FG_DIM,
                    borderwidth=0)
    # LabelFrame 標題（darkly 預設太暗，幾乎睇唔到；bootstyle=dark 會產生
    # "dark.TLabelframe.Label" 呢個 variant，要一併 configure）
    for lf in ("TLabelframe.Label", "dark.TLabelframe.Label"):
        style.configure(lf, foreground=FG_MAIN, font=(ui_font_family(), 10))
    # secondary 文字（提示/標籤）用調亮咗嘅 FG_DIM；secondary 按鈕文字用主色
    for lbl in ("secondary.TLabel", "info.TLabel"):
        style.configure(lbl, foreground=FG_DIM)
    style.configure("secondary.TButton", foreground=FG_MAIN)
    style.configure("Treeview.Heading", foreground=FG_DIM)
    return "darkly"
