# -*- coding: utf-8 -*-
"""time_spinbox — 時間碼輸入 + ▲▼ 微調（Entry + Canvas 自繪箭頭）。

為何唔用 ``ttk.Spinbox``：Spinbox 的箭頭本質係「數值 + increment」，而
``3:41.995`` 唔係數值 —— Tcl 的 ``string is double`` 失敗之後會**靜靜咁唔
做嘢**（冇例外、冇提示），變成「有箭頭但撳咗冇反應」，比冇箭頭更差。

為何唔用 ``▲`` / ``▼`` 字元：用戶 2026-11-05 實測「繪畫的箭咀不是 ▲▼ 的樣子」
—— U+25B2/25BC 喺 Windows 上會 fallback 去另一個字型，出到嚟係實心方塊或者
走位。**所以箭頭用 Canvas 畫多邊形**，形狀同大小完全自己控制，唔受字型影響，
亦順便做到 hover 高亮（真 spinner 的手感）。

箭頭語意：改**一個邊界**（開始欄的 ▲ = 開始時間 +10ms），移動整個字交返
面板的「平移」掣 —— 兩個動作唔應該搶同一個控制。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import ttkbootstrap as ttkb

from .. import theme, timefmt

#: 預設步進（ms）。同舊 Spinbox 的 increment=10 一致，唔改變用戶手感。
DEFAULT_STEP_MS = 10
#: 箭頭區闊度（px）——同原生 spinner 差不多，唔會食掉輸入框的位置。
ARROW_W = 15


class TimeSpinbox(ttkb.Frame):
    """時間碼輸入框 + 一對上下箭頭（Canvas 繪製）。

    Args:
        master: 父 widget。
        textvariable: 顯示用的 ``StringVar``（內容為時間碼或 ``—``）。
        step_ms: 每撳一下箭頭加減幾多毫秒。
        on_step: 撳箭頭後的回呼，收到試算後的新毫秒值；**回傳實際套用的值**
            （可以係夾過的值），回傳 ``None`` 代表「唔套用、畫面唔好郁」
            （例如未選中任何字）。冇提供就只改輸入框文字。
        width: 輸入框闊度（字元）。
        font: 輸入框字型（預設等寬字，防數字跳位）。
    """

    def __init__(self, master, *, textvariable, step_ms: int = DEFAULT_STEP_MS,
                 on_step: Callable[[int], int | None] | None = None,
                 width: int = 10, font: tuple | None = None) -> None:
        super().__init__(master, bootstyle="light")
        self._var = textvariable
        self._step = int(step_ms)
        self._on_step = on_step
        self._hot: str | None = None      # "up" / "down" / None（hover 高亮）

        self.entry = ttkb.Entry(self, textvariable=textvariable, width=width,
                                justify="right",
                                font=font or (theme.mono_font_family(), 9))
        self.entry.pack(side="left")

        self._canvas = tk.Canvas(self, width=ARROW_W,
                                 height=self.entry.winfo_reqheight(),
                                 highlightthickness=0, bd=0, cursor="hand2",
                                 takefocus=0)
        self._canvas.pack(side="left", fill="y")
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<Motion>", self._on_motion)
        self._canvas.bind("<Leave>", lambda _e: self._set_hot(None))
        self._canvas.bind("<Configure>", lambda _e: self._draw())
        self._draw()

    # ------------------------------------------------------------------
    # 繪圖
    # ------------------------------------------------------------------

    def _draw(self) -> None:
        """重畫兩個三角形（換主題 / 改大小 / hover 都會叫）。"""
        c = self._canvas
        w = max(int(c.winfo_width()), ARROW_W)
        h = max(int(c.winfo_height()), self.entry.winfo_reqheight())
        half = h / 2.0
        c.configure(bg=theme.BG_PANEL)
        c.delete("all")
        for name, y0, y1 in (("up", 0, half), ("down", half, h)):
            if self._hot == name:
                c.create_rectangle(0, y0, w, y1, fill=theme.BG_HOVER, outline="")
        c.create_line(0, half, w, half, fill=theme.BORDER)

        cx = w / 2.0
        ax = max(3.0, min(4.5, w / 3.4))       # 三角形半底寬
        ay = max(2.5, min(3.5, half / 3.2))    # 三角形半高
        mid_up, mid_dn = half / 2.0, half + half / 2.0
        c.create_polygon(cx, mid_up - ay, cx - ax, mid_up + ay, cx + ax, mid_up + ay,
                         fill=theme.FG_MAIN, outline="")
        c.create_polygon(cx, mid_dn + ay, cx - ax, mid_dn - ay, cx + ax, mid_dn - ay,
                         fill=theme.FG_MAIN, outline="")

    def restyle(self) -> None:
        """換介面主題後重畫（Canvas 唔會跟 ttk style 自動變色）。"""
        self._draw()

    # ------------------------------------------------------------------
    # 互動
    # ------------------------------------------------------------------

    def _hit(self, y: int) -> str:
        return "up" if y < self._canvas.winfo_height() / 2 else "down"

    def _on_click(self, event) -> str:
        self.step(+1 if self._hit(event.y) == "up" else -1)
        return "break"

    def _on_motion(self, event) -> None:
        self._set_hot(self._hit(event.y))

    def _set_hot(self, name: str | None) -> None:
        if name == self._hot:
            return
        self._hot = name
        self._draw()

    def step(self, direction: int, *, step_ms: int | None = None) -> int | None:
        """按一下箭頭。

        Args:
            direction: ``+1`` 加、``-1`` 減。
            step_ms: 覆寫步進。

        Returns:
            實際套用的毫秒值；``None`` = 冇改動（回呼拒絕，或已經夾到 0）。
        """
        step = self._step if step_ms is None else int(step_ms)
        try:
            current = timefmt.parse_time(self._var.get())
        except timefmt.TimeParseError:
            current = 0            # 未選中（顯示「—」）→ 由 0 起，但仍然交回呼決定
        new = max(0, current + direction * step)
        applied = new
        if self._on_step is not None:
            applied = self._on_step(new)
        if applied is None:
            return None
        self._var.set(timefmt.fmt_ms(applied))
        return applied

    def focus_entry(self) -> None:
        """把焦點放去輸入框（面板切換選取時用）。"""
        self.entry.focus_set()
        self.entry.select_range(0, "end")
