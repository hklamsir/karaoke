# -*- coding: utf-8 -*-
"""app — 主視窗：sidebar 導航 + 頁面切換 + 全域快捷鍵 + 狀態列（計劃書 §3.1 / A4）。

視窗結構（A4 決定）：單一視窗 + 左側 sidebar（``Frame.place`` + ``tkraise`` 切頁），
比 ttk.Notebook 更似現代 app，編輯器可以做到滿版 timeline。
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path
from typing import Any

import ttkbootstrap as ttkb

from . import settings, theme, version

LOGGER = logging.getLogger("karaoke.ui")

#: 圖示（`tools/make_icon.py` 生成；改設計就改該腳本再跑一次）
ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
ICON_ICO = ASSETS_DIR / "app.ico"
ICON_PNG = ASSETS_DIR / "app.png"

#: ←→ 游標步長（§6.2）：普通 ±100ms / Shift ±10ms / Ctrl ±1 帧 @60fps
FETCH_MS = 100
FINE_MS = 10
FRAME_MS = 17

#: 頁面註冊表：（key, 標題, 快捷鍵數字（配 Alt）, 模組路徑, 類名）
_PAGES: list[tuple[str, str, int, str, str]] = [
    ("home", "主頁", 1, "ui.pages.home", "HomePage"),
    ("editor", "編輯器", 2, "ui.pages.editor", "EditorPage"),
    ("settings", "設定", 3, "ui.pages.settings_page", "SettingsPage"),
    ("help", "幫助", 4, "ui.pages.help", "HelpPage"),
]

#: 切頁快捷鍵改成 Alt+數字。原本裸 1-4 綁喺 toplevel，會經 bindtags 傳到
#: ttk::entry —— 用戶喺編輯器打「1」就即刻跳去主頁（實際回報嘅 bug）。
#: 用 Tk 自己嘅 ``<Alt-Key-n>`` 匹配，唔自己解析 event.state 位元：
#: 各平台對 Alt 嘅 state 表示唔同（Windows 0x20000 / X11 Mod1 0x8），
#: 交給 Tk 內部修飾鍵表比對最準，而且裸數字鍵永遠唔會誤中。


class App(ttkb.Window):
    """卡拉OK字幕工具主視窗（1180×760 設計基準，§4.5.2）。"""

    def __init__(self) -> None:
        super().__init__(title=version.window_title(), size=(1180, 760), minsize=(960, 640))
        self.state("zoomed")   # 預設最大化（用戶需求：開窗即全畫面顯示）
        self._settings = settings.load()
        self._set_window_icon()

        # R1 對策：ttkbootstrap 相容性喺度驗證；失敗直接拋（唔靜默降級）
        # 主題：settings 記住上次選擇（litera / darkly / superhero），
        # apply_ttkbootstrap_theme 會先綁色票再 theme_use，所以 widget 建構時
        # 已經讀到正確色值（唔會「開窗一閃淺色」）。
        self._applied_theme = theme.apply_ttkbootstrap_theme(
            self, str(self._settings.get("theme") or theme.ACTIVE))

        self._pages: dict[str, Any] = {}
        self._nav_buttons: dict[str, ttkb.Button] = {}
        self._current_key: str = ""

        self._build_sidebar()
        self._build_statusbar()
        self._build_pages()
        # ttkbootstrap 嘅 bootstyle variant style（light.TFrame / light.TLabelframe…）
        # 係「第一次有 widget 用」先至建立 —— 早過 _build_pages 套色會被打回原形，
        # 所以建完所有頁面要再套一次色票（idempotent）。
        theme.apply_ttkbootstrap_theme(self)
        self._build_sidebar_handle()
        self._bind_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.show_page("home")
        LOGGER.info("UI 啟動完成（theme=%s）", self._applied_theme)

    def _on_close(self) -> None:
        """關視窗前：編輯器有未儲存修改就提示（E2）。"""
        editor = self._pages.get("editor")
        if editor is not None and hasattr(editor, "on_close_request"):
            if not editor.on_close_request():
                return
        self.destroy()

    # ------------------------------------------------------------------
    # 圖示 / 主題切換
    # ------------------------------------------------------------------

    def _set_window_icon(self) -> None:
        """設定視窗與工作列圖示（``assets/app.ico``，多尺寸 16–256）。

        ⚠️ 實測 ``wm iconbitmap`` 喺 ttkbootstrap Window 上**唔生效**（標題列仍然係
        ttkbootstrap 預設嘅羽毛圖示）。所以 Windows 直接用 ctypes 送 ``WM_SETICON``：
        ICON_SMALL（標題列）用 32px、ICON_BIG（Alt+Tab／工作列）用 256px。
        非 Windows 或 ico 缺失時退回 PNG ``iconphoto``。
        """
        if sys.platform == "win32" and ICON_ICO.is_file():
            # 要等視窗 map（hwnd 建立）之後才送得入 —— 太早 GetParent 會攞到 0，
            # 所以 _apply_win_icon 內建重試（實測 120ms 時 hwnd 仲未 ready）。
            self.after(300, self._apply_win_icon)
            return
        if not ICON_ICO.is_file():
            LOGGER.warning("找不到圖示 %s（用預設 tk 圖示）", ICON_ICO)
        try:
            if ICON_PNG.is_file():
                self._icon_photo = tk.PhotoImage(file=str(ICON_PNG))
                self.iconphoto(True, self._icon_photo)
        except Exception as exc:   # noqa: BLE001 — 圖示唔影響功能
            LOGGER.warning("設定 PNG 圖示失敗：%s", exc)

    def _apply_win_icon(self, attempt: int = 0) -> None:
        """Windows：LoadImage + WM_SETICON（iconbitmap 唔生效嘅可靠替代）。

        視窗未 map 之前 ``GetParent(winfo_id())`` 會攞到 0（實測 120ms 仍然未
        ready），所以失敗就每 200ms 重試，最多 15 次（≈3s）。
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetParent(self.winfo_id())
            if not hwnd:
                raise RuntimeError("hwnd 未建立")
            for size, flag in ((32, 0), (256, 1)):   # ICON_SMALL / ICON_BIG
                hicon = user32.LoadImageW(0, str(ICON_ICO), 1, size, size, 0x10)
                if hicon:
                    user32.SendMessageW(hwnd, 0x80, flag, hicon)   # WM_SETICON
            LOGGER.debug("視窗圖示已套用（attempt=%d）", attempt)
        except Exception as exc:   # noqa: BLE001
            if attempt < 15:
                self.after(200, lambda: self._apply_win_icon(attempt + 1))
            else:
                LOGGER.warning("WM_SETICON 多次失敗：%s", exc)

    def apply_theme(self, name: str) -> str:
        """即時切換主題（設定頁呼叫）：換色票 → 重設 ttk style → 重漆 tk 控件。

        ttk 控件靠 style 上色，``theme_use`` + 再用色票覆寫就即刻變色，**唔需要重建頁面**；
        tk 原生控件（Canvas / Text）唔受 style 影響，靠 ``restyle()`` / ``_theme_token``
        由 ``_restyle_tree`` 逐一補上。切換後寫入 settings.json，下次開窗沿用。

        Args:
            name: ``litera`` / ``darkly`` / ``superhero``。

        Returns:
            實際生效的主題 key。
        """
        key = theme.apply_ttkbootstrap_theme(self, name)
        self._applied_theme = key
        # 導航掣、品牌方塊嘅自訂 style 都含色值 → 重設
        self._nav_styles()
        self._side_handle.configure(bg=theme.BG_HOVER)
        self._restyle_tree(self)
        self.set_status(f"v0.1 · {key}")
        self.save_settings({**self._settings, "theme": key})
        LOGGER.info("主題已切換：%s", key)
        return key

    def _restyle_tree(self, widget) -> None:
        """遞歸為 tk 原生控件重新上色（ttk 控件會自己跟 style，唔使理）。

        兩種機制：
        1. widget 有 ``restyle()`` → 交給它（Canvas / Text 子類自己知點重畫）；
        2. widget 有 ``_theme_token`` 屬性（色票名稱）→ 直接 configure bg。
        """
        for child in widget.winfo_children():
            restyle = getattr(child, "restyle", None)
            if callable(restyle):
                try:
                    restyle()
                except Exception as exc:   # noqa: BLE001 — 個別控件重畫失敗唔阻其他
                    LOGGER.debug("restyle %r 失敗：%s", child, exc)
            token = getattr(child, "_theme_token", None)
            if token:
                try:
                    child.configure(bg=getattr(theme, token))
                except (tk.TclError, AttributeError):
                    pass
            self._restyle_tree(child)

    # ------------------------------------------------------------------
    # 版面
    # ------------------------------------------------------------------

    def _nav_styles(self) -> None:
        """導航按鈕專用 style：字級比主題預設 +2（9 → 11pt），顏色用專案色票。

        ttk::button **冇** ``-font`` option，字級只可以經 style 設；而 ``bootstyle``
        每次切頁都會改寫 style，所以自建 ``Nav`` / ``NavActive`` 兩個 style 自己管，
        避免「一切頁字級就彈返 9pt」。
        """
        style = ttkb.Style()
        font = (theme.ui_font_family(), self.NAV_FONT_SIZE)
        style.configure("Nav.TButton", font=font, background=theme.BG_HOVER,
                        foreground=theme.FG_MAIN, bordercolor=theme.BORDER,
                        lightcolor=theme.BG_HOVER, darkcolor=theme.BG_PANEL,
                        focusthickness=0, relief="flat", padding=(6, 4),
                        anchor="center")
        style.map("Nav.TButton",
                  background=[("pressed", theme.BORDER), ("active", theme.BORDER)],
                  foreground=[("disabled", theme.FG_DIMMER)])
        style.configure("NavActive.TButton", font=font, background=theme.ACCENT,
                        foreground=theme.ON_ACCENT, bordercolor=theme.ACCENT,
                        lightcolor=theme.ACCENT, darkcolor=theme.ACCENT_DIM,
                        focusthickness=0, relief="flat", padding=(6, 4),
                        anchor="center")
        style.map("NavActive.TButton",
                  background=[("pressed", theme.ACCENT_DIM), ("active", theme.ACCENT)],
                  foreground=[("disabled", theme.FG_DIMMER)])
        # 品牌方塊：藍底 + 18px 白字（負數 size = 像素）
        style.configure("Brand.TLabel", background=theme.ACCENT,
                        foreground=theme.ON_ACCENT,
                        font=(theme.ui_font_family(), -theme.BRAND_FONT_PX, "bold"),
                        padding=(5, 2))

    def _build_sidebar(self) -> None:
        """左側 sidebar（§4.5.3：標題 + 分隔線 + 4 項導航 + 底部狀態卡）。

        闊度由 ``self._sidebar_w`` 控制（用戶需求 #2：可拖曳調整），
        _relayout_sidebar() 統一重擺 sidebar / 分隔條 / 頁面 / 狀態列。
        """
        self._nav_styles()
        self._sidebar_w = theme.SIDEBAR_W
        bar = ttkb.Frame(self, width=self._sidebar_w, bootstyle="light")
        bar.place(x=0, y=0, width=self._sidebar_w, relheight=1.0)
        bar.pack_propagate(False)
        self._sidebar_bar = bar

        # 品牌：用戶需求 —— 文字「Karaoke AI Subtitler」**分兩行**（sidebar 只有 140px）。
        # 字級 18px：Tk 用 pt 表示，要**負數**先係像素（-18 = 18px @ 任何 DPI），
        # 寫 size=18 會變 18pt（≈24px）就爆格。兩行實測 `Karaoke` 78px /
        # `AI Subtitler` 104px，可視闊 140-16-10=114px，仍然放得落。
        # 顏色用自訂 style（Brand.TLabel）而唔係 inverse-<hex>：inverse 會用
        # ttkbootstrap 自己推算嘅對比色，深色主題下會變深字壓藍底（實測唔清楚）
        brand = ttkb.Label(bar, text="Karaoke\nAI Subtitler", style="Brand.TLabel",
                           anchor="w", justify="left")
        brand.pack(fill="x", padx=theme.SPACE, pady=(16, 4))
        self._brand_label = brand
        ttkb.Separator(bar).pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)

        for key, label, num, _, _ in _PAGES:
            # ttk::button **冇 -font option** → 字級要經獨立 style 設（見 _nav_styles）
            btn = ttkb.Button(
                bar, text=f"Alt+{num}  {label}", style="Nav.TButton",
                command=lambda k=key: self.show_page(k), takefocus=False)
            btn.pack(fill="x", padx=theme.SPACE, pady=2, ipady=6)
            self._nav_buttons[key] = btn

        # 底部 FFmpeg 狀態卡（§4.5.3：16,700）
        card = ttkb.Frame(bar, bootstyle="light")
        card.pack(side="bottom", fill="x", padx=theme.SPACE, pady=theme.SPACE)
        self._ffmpeg_status_var = tk.StringVar(value=self._tool_status_text())
        ttkb.Label(card, textvariable=self._ffmpeg_status_var, justify="left",
                   font=(theme.mono_font_family(), 8), bootstyle="secondary") \
            .pack(anchor="w")

    def _tool_status_text(self) -> str:
        ffmpeg = settings.detect_ffmpeg(self._settings)
        ffprobe = settings.detect_ffprobe(self._settings)
        key, _ = settings.resolve_api_key(self._settings)
        dot = "●" if ffmpeg else "○"
        kdot = "●" if key else "○"
        # sidebar 只有 140px：完整路徑放唔落（會斷成三行再被切走）→ 只顯示燈號，
        # 詳細路徑喺設定頁睇
        return f"{dot} ffmpeg　{kdot} api key"

    def _build_statusbar(self) -> None:
        """底部狀態列（§4.3：28px）。"""
        bar = ttkb.Frame(self, height=theme.STATUSBAR_H, bootstyle="light")
        bar.place(x=self._sidebar_w, rely=1.0, relwidth=1.0, y=-theme.STATUSBAR_H)
        self._statusbar_bar = bar
        self._status_var = tk.StringVar(value=f"v0.1 · {self._applied_theme}")
        ttkb.Label(bar, textvariable=self._status_var,
                   font=(theme.ui_font_family(), 9), bootstyle="secondary") \
            .pack(side="left", padx=theme.SPACE)

    def _build_pages(self) -> None:
        """lazy import 四個頁面，全部 place 滿版（sidebar / 狀態列以外）。"""
        import importlib

        for key, _, _, mod_name, cls_name in _PAGES:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            page = cls(self)
            self._place_page(page)
            self._pages[key] = page

    def _place_page(self, page) -> None:
        margin_x = self._sidebar_w + theme.SPACE
        page.place(in_=self, x=margin_x, y=theme.SPACE,
                   relwidth=1.0, width=-(margin_x + theme.SPACE),
                   relheight=1.0, height=-(theme.SPACE + theme.STATUSBAR_H + theme.SPACE))

    # ------------------------------------------------------------------
    # sidebar 闊度拖曳（用戶需求 #2）
    # ------------------------------------------------------------------

    #: sidebar 闊度範圍（theme.SIDEBAR_W = 140，仍可拖曳收窄／拉闊）
    SIDEBAR_MIN_W = 120
    SIDEBAR_MAX_W = 420

    #: 導航按鈕字級（用戶需求：比主題預設 +2 級 → 9 → 11pt）
    NAV_FONT_SIZE = 11

    def _build_sidebar_handle(self) -> None:
        """sidebar 右邊緣嘅垂直拖曳條：拉闊／收窄 sidebar + 同步重排頁面。"""
        self._side_handle = tk.Canvas(
            self, width=4, bg=theme.BG_HOVER, highlightthickness=0,
            cursor="sb_h_double_arrow")
        self._side_handle._theme_token = "BG_HOVER"   # 換主題時 _restyle_tree 用
        self._side_handle.place(x=self._sidebar_w, y=0, relheight=1.0)
        self._side_handle.bind("<Button-1>", self._on_side_handle_press)
        self._side_handle.bind("<B1-Motion>", self._on_side_handle_drag)
        self._side_handle.bind("<ButtonRelease-1>", lambda _e: self._relayout_sidebar())

    def _on_side_handle_press(self, event) -> None:
        pass   # B1-Motion 直接用指標位置計，唔需要 press state

    def _on_side_handle_drag(self, event) -> None:
        pointer_x = self._side_handle.winfo_x() + event.x   # root 內指標位置
        new_w = max(self.SIDEBAR_MIN_W, min(self.SIDEBAR_MAX_W, pointer_x))
        if new_w != self._sidebar_w:
            self._sidebar_w = new_w
            self._relayout_sidebar()

    def _relayout_sidebar(self) -> None:
        """sidebar 闊度變咗 → 重擺 sidebar / 分隔條 / 頁面 / 狀態列。"""
        self._sidebar_bar.place(x=0, y=0, width=self._sidebar_w, relheight=1.0)
        self._side_handle.place(x=self._sidebar_w, y=0, relheight=1.0)
        self._statusbar_bar.place(x=self._sidebar_w, rely=1.0, relwidth=1.0,
                                  y=-theme.STATUSBAR_H)
        for page in self._pages.values():
            self._place_page(page)

    # ------------------------------------------------------------------
    # 導航 / 快捷鍵
    # ------------------------------------------------------------------

    def show_page(self, key: str) -> None:
        """切頁（tkraise 頂上去）；頁面可以實作 ``on_show`` / ``on_hide`` hook。"""
        page = self._pages[key]
        prev = self._pages.get(self._current_key)
        if prev is not None and prev is not page and hasattr(prev, "on_hide"):
            prev.on_hide()
        page.tkraise()
        self._current_key = key
        for k, btn in self._nav_buttons.items():
            btn.configure(style="NavActive.TButton" if k == key else "Nav.TButton")
        if hasattr(page, "on_show"):
            page.on_show()
        self.set_status(f"v0.1 · {self._applied_theme}")

    @property
    def current_key(self) -> str:
        return self._current_key

    def set_status(self, text: str) -> None:
        self._status_var.set(text)

    def refresh_tool_status(self) -> None:
        self._ffmpeg_status_var.set(self._tool_status_text())

    def page(self, key: str) -> Any | None:
        """取已建立嘅頁面實例（頁面之間同步樣式用，例如編輯器 → 主頁）。

        Args:
            key: ``home`` / ``editor`` / ``settings`` / ``help``。

        Returns:
            頁面 widget；未註冊就 ``None``。
        """
        return self._pages.get(key)

    def open_editor_with(self, timings_path) -> None:
        """帶一份 timings.json 跳入編輯器（主頁「新任務完成」/「開啟現有」共用）。"""
        editor = self._pages["editor"]
        if hasattr(editor, "load_timings"):
            editor.load_timings(timings_path)
        self.show_page("editor")

    def _bind_shortcuts(self) -> None:
        """全域快捷鍵（§5.5.4 §3）：Alt+1-4 切頁；Ctrl+S/Z/Shift+Z/O；←→；空白鍵。

        切頁刻意唔用裸數字鍵 —— 綁喺 toplevel 嘅 ``<Key-1>`` 會經 bindtags
        落到 ttk::entry，變成「編輯器打 1 就跳主頁」；加 Alt 之後同文字輸入
        完全唔撞。
        """
        for key, _, num, _, _ in _PAGES:
            self.bind(f"<Alt-Key-{num}>", self._nav_handler(key))
        self.bind("<Control-s>", self._dispatch("save"))
        self.bind("<Control-z>", self._dispatch("undo"))
        self.bind("<Control-Z>", self._dispatch("redo"))     # Shift 已入 modifier 大寫
        self.bind("<Control-y>", self._dispatch("redo"))
        self.bind("<Left>", self._dispatch_cursor(-FETCH_MS, -FINE_MS, -FRAME_MS))
        self.bind("<Right>", self._dispatch_cursor(+FETCH_MS, +FINE_MS, +FRAME_MS))
        self.bind("<space>", self._dispatch("toggle_play"))

    def _nav_handler(self, key: str):
        """Alt+數字 切頁處理器。

        Args:
            key: 目標頁面 key（``home`` / ``editor`` / ``settings`` / ``help``）。

        Returns:
            回傳 ``"break"`` 嘅 Tk callback，阻止按鍵再傳落輸入控件。
        """
        def handler(_event=None) -> str:
            self.show_page(key)
            return "break"
        return handler

    def _dispatch(self, method: str):
        """轉發畀編輯器（save/undo/redo；淨係喺編輯器頁生效）。"""
        def handler(_event=None) -> str:
            if self._current_key != "editor":
                return "break"
            editor = self._pages.get("editor")
            if editor is not None and hasattr(editor, method):
                getattr(editor, method)()
            return "break"
        return handler

    def _dispatch_cursor(self, normal: int, fine: int, frame: int):
        def handler(event) -> str:
            editor = self._pages.get("editor")
            if self._current_key != "editor" or editor is None:
                return "break"
            if event.state & 0x0001:       # Shift
                editor.on_cursor_step(fine)
            elif event.state & 0x0004:     # Ctrl ≈ 1 帧 @60fps
                editor.on_cursor_step(frame)
            else:
                editor.on_cursor_step(normal)
            return "break"
        return handler

    # ------------------------------------------------------------------
    # 設定存取（頁面共用）
    # ------------------------------------------------------------------

    @property
    def settings_data(self) -> dict[str, Any]:
        return self._settings

    def save_settings(self, data: dict[str, Any]) -> None:
        self._settings = dict(data)
        settings.save(self._settings)
        self.refresh_tool_status()


def _set_app_user_model_id() -> None:
    """Windows：設定 AppUserModelID，令工作列用我哋嘅圖示（唔係 python.exe 圖示）。

    必須喺建立任何視窗**之前**呼叫，否則 Windows 已經用舊 ID 分組。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Lamsir.KaraokeAISubtitler")
    except Exception as exc:   # noqa: BLE001 — 只影響工作列圖示
        LOGGER.debug("設定 AppUserModelID 失敗：%s", exc)


def run() -> int:
    """啟動 UI。R1：ttkbootstrap import 失敗時畀清晰錯誤。"""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    _set_app_user_model_id()
    try:
        app = App()
    except Exception as exc:   # noqa: BLE001
        LOGGER.error("UI 啟動失敗：%s", exc)
        LOGGER.error("（ttkbootstrap 相容性問題時可改用原生 ttk，見計劃書 §12 假設 4）")
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
