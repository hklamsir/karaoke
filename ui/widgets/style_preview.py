# -*- coding: utf-8 -*-
"""style_preview — 主頁即時樣式預覽 canvas（計劃書 §2.3 B3 / §4.5.4）。

改任何樣式參數即刻重繪；畫法同 preview overlay 一致（8 向描邊 + 唱過/未唱分色），
但係喺純色底上渲染，唔使影片幀。
"""

from __future__ import annotations

import tkinter as tk

from .. import theme

#: 預覽畫布設計尺寸（§4.5.4：344×180）
PREVIEW_W = 344
PREVIEW_H = 180

DEFAULT_TEXT = "輪迴塵間的人"

#: 樣式預覽假定嘅「影片」解像度（縮放到 canvas 用）
SAMPLE_RES_X, SAMPLE_RES_Y = 1920, 1080


def hex_or(hex_color: str, fallback: str) -> str:
    """hex 顏色容錯：唔合法就返 fallback（畫布唔會因為打錯 hex 而爆）。"""
    value = (hex_color or "").strip().lstrip("#")
    if len(value) == 6:
        try:
            int(value, 16)
            return "#" + value.upper()
        except ValueError:
            pass
    return fallback


class StylePreviewCanvas(tk.Canvas):
    """即時樣式預覽：深色底 + 字幕文字（唱過部分 primary 色）+ 描邊。"""

    def __init__(self, master, width: int = PREVIEW_W, height: int = PREVIEW_H) -> None:
        super().__init__(master, width=width, height=height,
                         bg=theme.BG_DEEP, highlightthickness=1,
                         highlightbackground=theme.BORDER)
        self._width, self._height = width, height
        self._style: dict = {}
        self.set_style(text=DEFAULT_TEXT)

    def set_style(self, *, text: str = DEFAULT_TEXT, font_name: str | None = None,
                  font_size: int = 48, primary_color: str = theme.SUB_PRIMARY,
                  secondary_color: str = theme.SUB_SECONDARY,
                  outline_color: str = "#000000", outline_width: float = 0.0,
                  margin_v: int = 0, sung_chars: int = 3) -> None:
        """更新樣式並即時重繪。``sung_chars`` = 由頭數起幾多隻字算「唱過」。"""
        self._style = dict(text=text, font_name=font_name or theme.ui_font_family(),
                           font_size=font_size, primary_color=primary_color,
                           secondary_color=secondary_color, outline_color=outline_color,
                           outline_width=outline_width, margin_v=margin_v,
                           sung_chars=sung_chars)
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        s = self._style
        w, h = self._width, self._height

        # 16:9 模擬影片框（留 8px 邊）
        frame_w = min(w - 16, (h - 16) * SAMPLE_RES_X // SAMPLE_RES_Y)
        frame_h = frame_w * SAMPLE_RES_Y // SAMPLE_RES_X
        fx, fy = (w - frame_w) // 2, (h - frame_h) // 2
        self.create_rectangle(fx, fy, fx + frame_w, fy + frame_h,
                              fill="#101016", outline=theme.BORDER)

        scale = frame_h / SAMPLE_RES_Y     # PlayRes → 畫布縮放
        size = max(10, round(s["font_size"] * scale))
        # margin_v=0 時同 ASS 一致自動推算（StyleConfig.resolved：1080p ≈ 59px）
        raw_margin = s["margin_v"] or max(24, round(SAMPLE_RES_Y * 0.055))
        margin_v = max(0, round(raw_margin * scale))
        outline = max(1.0, (s["outline_width"] or 3.0) * scale)

        family = s["font_name"] if s["font_name"] in tk_font_families_safe() \
            else theme.ui_font_family()
        text = s["text"] or DEFAULT_TEXT
        sung = max(0, min(s["sung_chars"], len(text)))

        # 底部置中（ASS Alignment=2）
        font = (family, size)
        total_w = sum(self._char_w(ch, font) for ch in text)
        x = fx + (frame_w - total_w) / 2
        y = fy + frame_h - margin_v - size - size * 0.25

        for i, ch in enumerate(text):
            color = hex_or(s["primary_color"], theme.SUB_PRIMARY) if i < sung \
                else hex_or(s["secondary_color"], theme.SUB_SECONDARY)
            self._draw_char(x, y, ch, font, color, hex_or(s["outline_color"], "#000000"), outline)
            x += self._char_w(ch, font)

    def _char_w(self, ch: str, font: tuple) -> int:
        return self.tk.call("font", "measure", font, ch) + 1

    def _draw_char(self, x: float, y: float, ch: str, font: tuple,
                   color: str, outline_color: str, outline: float) -> None:
        """tkinter Canvas 無原生描邊 → 8 方向疊同色文字模擬（§6.7.5）。"""
        steps = max(1, int(outline))
        for dx in range(-steps, steps + 1, max(1, steps // 1)):
            for dy in range(-steps, steps + 1, max(1, steps // 1)):
                if dx or dy:
                    self.create_text(x + dx, y + dy, text=ch, font=font,
                                     fill=outline_color, anchor="nw")
        self.create_text(x, y, text=ch, font=font, fill=color, anchor="nw")


def tk_font_families_safe() -> list[str]:
    try:
        import tkinter.font as tkfont
        root = tkfont._get_default_root()   # noqa: SLF001
        return list(tkfont.families(root))
    except Exception:   # noqa: BLE001
        return []
