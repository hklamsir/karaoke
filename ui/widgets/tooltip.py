# -*- coding: utf-8 -*-
"""tooltip — 輕量工具提示（tk.Toplevel，唔用第三方套件）。

為咗「圖示按鈕冇文字講明做乜」而加：滑鼠停留 450ms 就喺指標右下方彈一個
細標籤；離開／按下就即刻收。全部 widget 共用一個實例都可以，但每個 widget
一個 Tooltip 物件最簡單（數量少，成本可忽略）。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .. import theme

DELAY_MS = 450


class Tooltip:
    """把一段說明文字掛喺 widget 上。

    Args:
        widget: 目標 widget。
        text: 說明文字；可以係 ``str`` 或者 ``callable() -> str``（動態更新，
            例如「復原：移動字塊」）。``callable`` 只會喺真正彈出時才呼叫。
        delay: 停留幾多毫秒先彈（預設 450ms）。
    """

    def __init__(self, widget: tk.Misc, text: str | Callable[[], str],
                 *, delay: int = DELAY_MS) -> None:
        self._widget = widget
        self._text = text
        self._delay = delay
        self._after_id: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    # ------------------------------------------------------------------

    def set_text(self, text: str | Callable[[], str]) -> None:
        """換說明文字（動態內容用）。"""
        self._text = text

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self._widget.after(self._delay, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except (tk.TclError, ValueError):
                pass
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        text = self._text() if callable(self._text) else self._text
        if not text:
            return
        try:
            x = self._widget.winfo_rootx() + 8
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 6
        except tk.TclError:
            return
        win = tk.Toplevel(self._widget)
        win.wm_overrideredirect(True)
        win.configure(bg=theme.BORDER)
        frame = tk.Frame(win, bg=theme.FG_MAIN, padx=8, pady=4)
        frame.pack(padx=1, pady=1)
        tk.Label(frame, text=text, bg=theme.FG_MAIN, fg="#FFFFFF",
                 justify="left", font=(theme.ui_font_family(), 9)).pack()
        win.update_idletasks()
        # 唔好彈出畫面右／下邊界
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.wm_geometry(f"+{min(x, sw - w - 8)}+{min(y, sh - h - 8)}")
        self._win = win

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None
