# -*- coding: utf-8 -*-
"""style_preview — 主頁即時樣式預覽 canvas（計劃書 §2.3 B3 / §4.5.4）。

改任何樣式參數即刻重繪；畫法同 preview overlay 一致（8 向描邊 + 唱過/未唱分色），
但係喺純色底上渲染，唔使影片幀。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

from .. import theme
from .overlay import pixel_font

#: 預覽畫布設計尺寸（§4.5.4：344×180）
PREVIEW_W = 344
PREVIEW_H = 180

DEFAULT_TEXT = "輪迴塵間的人"

#: 雙行預覽時上行顯示的「下一句」示意（純裝飾，唔會寫入任何檔案）
NEXT_TEXT = "事過境遷以後不再提起"

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
                  margin_v: int = 0, sung_chars: int = 3,
                  dual_line: bool = False, alignment: int = 2,
                  up_offset_pct: int = 0, down_offset_pct: int = 0) -> None:
        """更新樣式並即時重繪。``sung_chars`` = 由頭數起幾多隻字算「唱過」。"""
        self._style = dict(text=text, font_name=font_name or theme.ui_font_family(),
                           font_size=font_size, primary_color=primary_color,
                           secondary_color=secondary_color, outline_color=outline_color,
                           outline_width=outline_width, margin_v=margin_v,
                           sung_chars=sung_chars, dual_line=dual_line,
                           alignment=alignment, up_offset_pct=up_offset_pct,
                           down_offset_pct=down_offset_pct)
        self._redraw()

    def restyle(self) -> None:
        """換主題：底色／邊框跟色票再重繪。"""
        self.configure(bg=theme.BG_DEEP, highlightbackground=theme.BORDER)
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
                              fill=theme.VIDEO_BG, outline=theme.BORDER)

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

        # ADR-030：垂直位置跟「對齊」（2 底 / 8 頂 / 5 中）
        # ⚠️ 字級用「像素」單位（pixel_font → 負 size）：libass Fontsize 係像素，
        #    Tk 正數係點，用正數會令預覽大 4/3（用戶回報 #3）
        font = pixel_font(family, size)
        an = int(s.get("alignment") or 2)
        if an == 8:
            y = fy + margin_v
        elif an == 5:
            y = fy + frame_h / 2 - size / 2
        else:
            y = fy + frame_h - margin_v - size - size * 0.25

        def draw_line(txt: str, top_y: float, sung_count: int,
                      offset_pct: float = 0.0) -> None:
            total_w = sum(self._char_w(ch, font) for ch in txt)
            x = fx + (frame_w - total_w) / 2 + frame_w * offset_pct / 100.0
            for i, ch in enumerate(txt):
                color = hex_or(s["primary_color"], theme.SUB_PRIMARY) if i < sung_count \
                    else hex_or(s["secondary_color"], theme.SUB_SECONDARY)
                self._draw_char(x, top_y, ch, font, color,
                                hex_or(s["outline_color"], "#000000"), outline)
                x += self._char_w(ch, font)

        up_pct = float(s.get("up_offset_pct") or 0)
        down_pct = float(s.get("down_offset_pct") or 0)
        if s.get("dual_line"):
            gap = round(max(24, s["font_size"] * 1.45) * scale)
            if an == 8:
                up_y, down_y = y, y + gap
            elif an == 5:
                up_y, down_y = y - gap / 2, y + gap / 2
            else:
                up_y, down_y = y - gap, y
            # 上行 = 下一句（全未唱色）；下行 = 當前句
            draw_line(NEXT_TEXT, up_y, 0, up_pct)
            draw_line(text, down_y, sung, down_pct)
        else:
            draw_line(text, y, sung)

    def _char_w(self, ch: str, font: tkfont.Font) -> int:
        return font.measure(ch) + 1

    def _draw_char(self, x: float, y: float, ch: str, font: tkfont.Font,
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
    """列出系統字型（唔會拋）。

    ⚠️ 以前寫 `tkfont._get_default_root()` —— 呢個私有函數**唔存在於
    `tkinter.font`**（喺 `tkinter` 模組），所以每次呼叫都 AttributeError，
    被 `except` 吞掉之後永遠回傳空 list，字型下拉只剩預設一個（用戶回報
    「揀唔到中文字型」就係呢個）。`tkfont.families()` 唔傳 root 會自動用
    default root，實測 Windows 可列出 400+ 個字型。

    另外過濾 `@` 開頭的重複項（垂直書寫版本，例如 `@MingLiU`）。
    """
    try:
        import tkinter.font as tkfont
        return sorted({name for name in tkfont.families() if not name.startswith("@")})
    except Exception:   # noqa: BLE001
        return []
