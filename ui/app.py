# -*- coding: utf-8 -*-
"""app — 主視窗：sidebar 導航 + 頁面切換 + 全域快捷鍵 + 狀態列（計劃書 §3.1 / A4）。

視窗結構（A4 決定）：單一視窗 + 左側 sidebar（``Frame.place`` + ``tkraise`` 切頁），
比 ttk.Notebook 更似現代 app，編輯器可以做到滿版 timeline。
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from typing import Any

import ttkbootstrap as ttkb

from . import settings, theme

LOGGER = logging.getLogger("karaoke.ui")

#: 頁面註冊表：（key, 標題, 快捷鍵數字 1-4, 模組路徑, 類名）
_PAGES: list[tuple[str, str, int, str, str]] = [
    ("home", "主頁", 1, "ui.pages.home", "HomePage"),
    ("editor", "編輯器", 2, "ui.pages.editor", "EditorPage"),
    ("settings", "設定", 3, "ui.pages.settings_page", "SettingsPage"),
    ("help", "幫助", 4, "ui.pages.help", "HelpPage"),
]


class App(ttkb.Window):
    """卡拉OK字幕工具主視窗（1180×760 設計基準，§4.5.2）。"""

    def __init__(self) -> None:
        super().__init__(title="Karaoke 字幕編輯器", size=(1180, 760), minsize=(960, 640))
        self.place_window_center()
        self._settings = settings.load()

        # R1 對策：ttkbootstrap 相容性喺度驗證；失敗直接拋（唔靜默降級）
        self._applied_theme = theme.apply_ttkbootstrap_theme(self)

        self._pages: dict[str, Any] = {}
        self._nav_buttons: dict[str, ttkb.Button] = {}
        self._current_key: str = ""

        self._build_sidebar()
        self._build_statusbar()
        self._build_pages()
        self._bind_shortcuts()

        self.show_page("home")
        LOGGER.info("UI 啟動完成（theme=%s）", self._applied_theme)

    # ------------------------------------------------------------------
    # 版面
    # ------------------------------------------------------------------

    def _build_sidebar(self) -> None:
        """左側 sidebar（§4.5.3：標題 + 分隔線 + 4 項導航 + 底部狀態卡）。"""
        bar = ttkb.Frame(self, width=theme.SIDEBAR_W, bootstyle="dark")
        bar.place(x=0, y=0, width=theme.SIDEBAR_W, relheight=1.0)
        bar.pack_propagate(False)

        ttkb.Label(bar, text="Karaoke", font=(theme.ui_font_family(), 16, "bold"),
                   bootstyle=f"inverse-{theme.ACCENT}") \
            .pack(fill="x", padx=theme.SPACE, pady=(16, 4))
        ttkb.Separator(bar).pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)

        for key, label, num, _, _ in _PAGES:
            btn = ttkb.Button(
                bar, text=f"{num}  {label}", bootstyle="secondary",
                command=lambda k=key: self.show_page(k), takefocus=False)
            btn.pack(fill="x", padx=theme.SPACE, pady=2, ipady=6)
            self._nav_buttons[key] = btn

        # 底部 FFmpeg 狀態卡（§4.5.3：16,700）
        card = ttkb.Frame(bar, bootstyle="dark")
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
        return f"{dot} ffmpeg   {kdot} api key\n  {ffmpeg or '未搵到 ffmpeg'}"

    def _build_statusbar(self) -> None:
        """底部狀態列（§4.3：28px）。"""
        bar = ttkb.Frame(self, height=theme.STATUSBAR_H, bootstyle="dark")
        bar.place(x=theme.SIDEBAR_W, rely=1.0, relwidth=1.0, y=-theme.STATUSBAR_H)
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
            margin_x = theme.SIDEBAR_W + theme.SPACE
            page.place(in_=self, x=margin_x, y=theme.SPACE,
                       relwidth=1.0, width=-(margin_x + theme.SPACE),
                       relheight=1.0, height=-(theme.SPACE + theme.STATUSBAR_H + theme.SPACE))
            self._pages[key] = page

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
            btn.configure(bootstyle="primary" if k == key else "secondary")
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

    def open_editor_with(self, timings_path) -> None:
        """帶一份 timings.json 跳入編輯器（主頁「新任務完成」/「開啟現有」共用）。"""
        editor = self._pages["editor"]
        if hasattr(editor, "load_timings"):
            editor.load_timings(timings_path)
        self.show_page("editor")

    def _bind_shortcuts(self) -> None:
        """全域快捷鍵（§5.5.4 §3）：數字鍵 1-4 切頁。"""
        for key, _, num, _, _ in _PAGES:
            self.bind(f"<Key-{num}>", lambda _e, k=key: self.show_page(k))

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


def run() -> int:
    """啟動 UI。R1：ttkbootstrap import 失敗時畀清晰錯誤。"""
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
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
