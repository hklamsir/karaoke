# -*- coding: utf-8 -*-
"""theme — ttkbootstrap 主題 + 自訂色票（計劃書 §4.1）+ 字型（§4.2）+ 間距（§4.3）。

色票原則（2026-11-02 改版）：**多主題色票** —— 用戶回報唔可以只剩一套，於是
``PALETTES`` 內建三套（litera 淺色 / darkly 炭灰 / superhero 深藍橙），
``set_palette(name)`` 會把對應色值**重新綁定到本模組的全局名稱**，
所以 widget 一律寫 ``theme.BG_PANEL``（**屬性存取**，唔好 ``from theme import BG_PANEL``）
就自動跟隨當前主題。

- 所有 Canvas（時間軸、波形、字塊、概覽條）用 ``CANVAS_*`` / ``WAVE_*`` /
  ``BLOCK_*`` token，**唔好再喺 widget 內寫死 hex**，否則換色就會半深半淺；
- 字幕色（``SUB_PRIMARY`` / ``SUB_SECONDARY``）**唔跟 UI 走** —— 佢係燒錄出片嘅顏色。
"""

from __future__ import annotations

import logging
import tkinter.font as tkfont

LOGGER = logging.getLogger("karaoke.ui.theme")

# ---------------------------------------------------------------------------
# 色票（§4.1）—— 三套主題
# ---------------------------------------------------------------------------
#: 每套色票的 token 必須完全一致（少一個 key 會喺 set_palette 直接拋 KeyError，
#: 寧願啟動就爆都唔好半深半淺）
PALETTES: dict[str, dict[str, str]] = {
    # ---------------- litera：淺色（白底黑字）----------------
    "litera": {
        "BG_DEEP": "#FFFFFF",         # 主視窗底（最淺）
        "BG_PANEL": "#F6F7F9",        # sidebar / 卡片 / 面板
        "BG_HOVER": "#E8ECF2",        # 按鈕 hover、輸入框 focus、選取底
        "BORDER": "#D3D8E0",          # 分隔線、格線
        "FG_MAIN": "#16181D",         # 文字（主）
        "FG_DIM": "#4E5666",          # 文字（次）
        "FG_DIMMER": "#8B93A1",       # 文字（裝飾性極次：tick 標籤等）
        "ACCENT": "#1F6FEB",          # 主按鈕、focus ring、選取行、游標線
        "ACCENT_DIM": "#1552B0",      # accent pressed
        "ON_ACCENT": "#FFFFFF",       # accent 底上的文字
        "SUCCESS": "#1F9254",
        "WARNING": "#B26A00",         # 「估算字」標記（淺底要夠深先睇得清）
        "DANGER": "#D33A3A",
        "CANVAS_BG": "#FFFFFF",       # 時間軸畫布底
        "WAVE_FILL": "#C9DDF8",
        "WAVE_LINE": "#1F6FEB",
        "BLOCK_FILL": "#DCE9FA",
        "BLOCK_FILL_CURRENT": "#BBD4F5",
        "BLOCK_OUTLINE": "#8FB3E0",
        "BLOCK_EST_FILL": "#FDEFD3",
        "BLOCK_TEXT": "#16181D",
        "GAP_FILL": "#F2F4F7",
        "GAP_LINE": "#DDE2E9",
        "OVERVIEW_BG": "#F3F5F8",
        "OVERVIEW_MARK": "#C3CBD6",
        "CODE_FG": "#0B4FA8",         # 行內碼 / 碼塊文字
        "SEARCH_HIT": "#FFE08A",      # 說明頁搜尋命中底
        "VIDEO_BG": "#0E0E12",        # 影片區（刻意深色）
    },
    # ---------------- darkly：炭灰（ttkbootstrap 經典深色）----------------
    "darkly": {
        "BG_DEEP": "#1B1B1E",
        "BG_PANEL": "#232327",
        "BG_HOVER": "#2F2F36",
        "BORDER": "#3D3D46",
        "FG_MAIN": "#E7E9ED",
        "FG_DIM": "#A9B0BB",
        "FG_DIMMER": "#727A86",
        "ACCENT": "#4C8DFF",
        "ACCENT_DIM": "#2F6FD0",
        "ON_ACCENT": "#FFFFFF",
        "SUCCESS": "#2FB344",
        "WARNING": "#E0A32E",
        "DANGER": "#E5534B",
        "CANVAS_BG": "#1B1B1E",
        "WAVE_FILL": "#2C3E56",
        "WAVE_LINE": "#5AA0FF",
        "BLOCK_FILL": "#2A3C52",
        "BLOCK_FILL_CURRENT": "#3B5573",
        "BLOCK_OUTLINE": "#4C6685",
        "BLOCK_EST_FILL": "#4A3A1C",
        "BLOCK_TEXT": "#E7E9ED",
        "GAP_FILL": "#202026",
        "GAP_LINE": "#2C2C34",
        "OVERVIEW_BG": "#141417",
        "OVERVIEW_MARK": "#4A4A55",
        "CODE_FG": "#8FC7FF",
        "SEARCH_HIT": "#7A5B12",
        "VIDEO_BG": "#0E0E12",
    },
    # ---------------- superhero：深藍 + 橙（ttkbootstrap 高對比深色）----------------
    "superhero": {
        "BG_DEEP": "#22303D",
        "BG_PANEL": "#2B3E50",
        "BG_HOVER": "#374C60",
        "BORDER": "#46607A",
        "FG_MAIN": "#EDF1F5",
        "FG_DIM": "#B4C3D1",
        "FG_DIMMER": "#7B8E9F",
        "ACCENT": "#DF691A",
        "ACCENT_DIM": "#B85412",
        "ON_ACCENT": "#FFFFFF",
        "SUCCESS": "#5CB85C",
        "WARNING": "#EBAA3E",
        "DANGER": "#D9534F",
        "CANVAS_BG": "#22303D",
        "WAVE_FILL": "#3A5370",
        "WAVE_LINE": "#7FB3E8",
        "BLOCK_FILL": "#33495C",
        "BLOCK_FILL_CURRENT": "#465E74",
        "BLOCK_OUTLINE": "#58738C",
        "BLOCK_EST_FILL": "#5A4420",
        "BLOCK_TEXT": "#EDF1F5",
        "GAP_FILL": "#263847",
        "GAP_LINE": "#33485A",
        "OVERVIEW_BG": "#1A2632",
        "OVERVIEW_MARK": "#4E6376",
        "CODE_FG": "#FFCF8A",
        "SEARCH_HIT": "#7A5A18",
        "VIDEO_BG": "#0E0E12",
    },
}

#: 設定頁下拉顯示用：（key, 中文標籤）
THEME_CHOICES: tuple[tuple[str, str], ...] = (
    ("litera", "淺色 · Litera（白底黑字）"),
    ("darkly", "深色 · Darkly（炭灰）"),
    ("superhero", "深色 · Superhero（深藍橙）"),
)

#: 色票 key → ttkbootstrap base 主題名（同 key 一樣，保留 mapping 方便日後調整）
BASE_THEMES: dict[str, str] = {"litera": "litera", "darkly": "darkly",
                              "superhero": "superhero"}

#: 目前套用中的主題（``set_palette`` 會更新）
ACTIVE = "litera"

# ---------------------------------------------------------------------------
# 全局色票名稱（由 set_palette 重新綁定，widget 要經 ``theme.XXX`` 存取）
# ---------------------------------------------------------------------------

BG_DEEP = PALETTES[ACTIVE]["BG_DEEP"]
BG_PANEL = PALETTES[ACTIVE]["BG_PANEL"]
BG_HOVER = PALETTES[ACTIVE]["BG_HOVER"]
BORDER = PALETTES[ACTIVE]["BORDER"]
FG_MAIN = PALETTES[ACTIVE]["FG_MAIN"]
FG_DIM = PALETTES[ACTIVE]["FG_DIM"]
FG_DIMMER = PALETTES[ACTIVE]["FG_DIMMER"]
ACCENT = PALETTES[ACTIVE]["ACCENT"]
ACCENT_DIM = PALETTES[ACTIVE]["ACCENT_DIM"]
ON_ACCENT = PALETTES[ACTIVE]["ON_ACCENT"]
SUCCESS = PALETTES[ACTIVE]["SUCCESS"]
WARNING = PALETTES[ACTIVE]["WARNING"]
DANGER = PALETTES[ACTIVE]["DANGER"]
CANVAS_BG = PALETTES[ACTIVE]["CANVAS_BG"]
WAVE_FILL = PALETTES[ACTIVE]["WAVE_FILL"]
WAVE_LINE = PALETTES[ACTIVE]["WAVE_LINE"]
BLOCK_FILL = PALETTES[ACTIVE]["BLOCK_FILL"]
BLOCK_FILL_CURRENT = PALETTES[ACTIVE]["BLOCK_FILL_CURRENT"]
BLOCK_OUTLINE = PALETTES[ACTIVE]["BLOCK_OUTLINE"]
BLOCK_EST_FILL = PALETTES[ACTIVE]["BLOCK_EST_FILL"]
BLOCK_TEXT = PALETTES[ACTIVE]["BLOCK_TEXT"]
GAP_FILL = PALETTES[ACTIVE]["GAP_FILL"]
GAP_LINE = PALETTES[ACTIVE]["GAP_LINE"]
OVERVIEW_BG = PALETTES[ACTIVE]["OVERVIEW_BG"]
OVERVIEW_MARK = PALETTES[ACTIVE]["OVERVIEW_MARK"]
CODE_FG = PALETTES[ACTIVE]["CODE_FG"]
SEARCH_HIT = PALETTES[ACTIVE]["SEARCH_HIT"]
VIDEO_BG = PALETTES[ACTIVE]["VIDEO_BG"]

#: 字幕「唱過 / 未唱」預設色 —— **保留專案原值**，同 timings.json 一致，不隨 UI 主題變
SUB_PRIMARY = "#FFFF00"
SUB_SECONDARY = "#FFFFFF"


def set_palette(name: str) -> str:
    """切換色票（只改本模組全局值；不會動 ttk style，之後要再呼叫 apply）。

    Args:
        name: ``litera`` / ``darkly`` / ``superhero``；未知值會 fallback 去 litera。

    Returns:
        實際生效的主題名。
    """
    global ACTIVE
    key = name if name in PALETTES else "litera"
    if key != name:
        LOGGER.warning("未知主題 %r → 改用 litera", name)
    values = PALETTES[key]
    missing = set(PALETTES["litera"]) - set(values)
    if missing:      # pragma: no cover — 開發期保護
        raise KeyError(f"色票 {key} 缺少 token：{sorted(missing)}")
    globals().update(values)
    ACTIVE = key
    return key


def is_dark(name: str | None = None) -> bool:
    """該主題係咪深色（決定 icon／陰影等細節）。"""
    return (name or ACTIVE) != "litera"


# ---------------------------------------------------------------------------
# 字型（§4.2）
# ---------------------------------------------------------------------------

FONT_UI = "Microsoft JhengHei UI"       # 介面（中文）
FONT_UI_LATIN = "Segoe UI"              # 介面（西文 fallback 用）
FONT_MONO = "Cascadia Mono"             # 數值（ms / 時間碼）—— 等寬防跳（功能需求）

#: 介面基準字級（pt）。用戶回報「字體很小」→ 9 統一升到 10
UI_FONT_SIZE = 10

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
SIDEBAR_W = 140      # sidebar 總寬（用戶需求：160 → 140px；可拖曳 120~420）
TOOLBAR_H = 48       # 工具列高
STATUSBAR_H = 28     # 狀態列高
ROW_H = 28           # Treeview 行高
CHAR_MIN_W = 3       # 時間軸最小 char 寬度（細過就聚合顯示）

#: 品牌方塊字級 —— 用**像素**（Tk 負數 size = px），用戶指定 18px
BRAND_FONT_PX = 18


def apply_ttkbootstrap_theme(root, name: str | None = None) -> str:
    """套用指定主題 + 用當前色票覆寫（底板 / 輸入框 / Treeview / 標題 / 預設字級）。

    可重複呼叫（啟動時、換主題時）；每次都會先 ``theme_use`` 再用色票覆寫，
    所以唔會被 ttkbootstrap 嘅預設值蓋返。

    Args:
        root: 主視窗（改具名字型要用 root 做 context）。
        name: 主題 key；``None`` 用 ``ACTIVE``。

    Returns:
        實際套用嘅主題名（key）。
    """
    import ttkbootstrap as ttkb

    key = set_palette(name or ACTIVE)
    base = BASE_THEMES.get(key, "litera")
    style = ttkb.Style.get_instance()
    try:
        style.theme_use(base)
    except Exception as exc:   # noqa: BLE001 — 個別版本唔支援就沿用現有 base
        LOGGER.warning("切換 base 主題 %s 失敗（沿用現有）：%s", base, exc)

    # 介面基準字級（Tk 具名字型；影響所有冇自己設 font 嘅 ttk widget）
    for fname in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                  "TkCaptionFont", "TkSmallCaptionFont", "TkTooltipFont"):
        try:
            f = tkfont.nametofont(fname, root=root)
            f.configure(family=ui_font_family(), size=UI_FONT_SIZE)
        except Exception:   # noqa: BLE001 — 個別具名字型唔存在就跳過
            pass

    # 底板 / 卡片：一律用當前色票（light./dark. variant 一併覆寫，避免換主題殘留）
    for style_name in ("TFrame", "light.TFrame", "dark.TFrame", "secondary.TFrame",
                       "info.TFrame", "TLabelframe", "light.TLabelframe",
                       "dark.TLabelframe"):
        try:
            style.configure(style_name, background=BG_PANEL
                            if "Labelframe" in style_name else BG_DEEP)
        except Exception:   # noqa: BLE001
            pass
    for style_name in ("TLabelframe", "light.TLabelframe", "dark.TLabelframe"):
        try:
            style.configure(style_name, bordercolor=BORDER, borderwidth=1,
                            relief="solid")
        except Exception:   # noqa: BLE001
            pass

    # 輸入框 / 下拉框 / 微調框：面板底 + 邊框 + 主字色
    for element in ("TEntry", "TCombobox", "TSpinbox"):
        try:
            style.configure(element, fieldbackground=BG_DEEP, background=BG_DEEP,
                            foreground=FG_MAIN, bordercolor=BORDER,
                            lightcolor=BG_DEEP, darkcolor=BG_DEEP,
                            insertcolor=FG_MAIN)
        except Exception:   # noqa: BLE001 — 個別 element 唔存在就跳過
            pass

    # Treeview：行高 28 + 主底 + 選取行 accent
    style.configure("Treeview", rowheight=28, background=BG_DEEP,
                    fieldbackground=BG_DEEP, foreground=FG_MAIN,
                    bordercolor=BORDER)
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", ON_ACCENT)])
    # 標題列：bootstyle="light" 嘅 Treeview 用嘅係 light.Treeview.Heading，
    # 只改 "Treeview.Heading" 唔夠 → 深色主題會見到淺灰標題（實測）
    for heading in ("Treeview.Heading", "light.Treeview.Heading",
                    "dark.Treeview.Heading"):
        try:
            style.configure(heading, background=BG_HOVER, foreground=FG_DIM,
                            bordercolor=BORDER, borderwidth=0, relief="flat")
            style.map(heading, background=[("active", BG_PANEL),
                                          ("pressed", BG_PANEL)])
        except Exception:   # noqa: BLE001
            pass

    # LabelFrame 標題：跟主題色（換淺／深都要睇得清）
    for lf in ("TLabelframe.Label", "light.TLabelframe.Label",
               "dark.TLabelframe.Label"):
        try:
            style.configure(lf, foreground=FG_MAIN, background=BG_PANEL,
                            font=(ui_font_family(), 10))
        except Exception:   # noqa: BLE001
            pass

    # 文字類：主文 / 次文 / 反白
    style.configure("TLabel", foreground=FG_MAIN, background=BG_DEEP)
    for lbl in ("secondary.TLabel", "info.TLabel"):
        style.configure(lbl, foreground=FG_DIM, background=BG_DEEP)
    for lbl in ("light.TLabel", "dark.TLabel"):
        style.configure(lbl, foreground=FG_MAIN, background=BG_DEEP)
    for lbl in ("inverse.TLabel",):
        style.configure(lbl, foreground=ON_ACCENT, background=ACCENT)
    style.configure("secondary.TButton", foreground=FG_MAIN)
    style.configure("secondary.Outline.TButton", foreground=FG_MAIN,
                    bordercolor=BORDER)
    # 分隔線 / 捲軸
    try:
        style.configure("TSeparator", background=BORDER)
    except Exception:   # noqa: BLE001
        pass
    return key
