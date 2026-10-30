# -*- coding: utf-8 -*-
"""overlay — 字幕疊圖渲染（計劃書 §6.7.5：大 preview 窗嘅真正價值）。

tkinter.Canvas 直接畫字（唔用 libass）：
- 字級縮放 = font_size × (畫布高 / play_res_y) —— 跟原片比例唔走位
- 底部置中（ASS Alignment=2）；margin_v 同樣按比例
- 游標之前嘅字 = primary（唱過）；之後 = secondary（未唱）
- Canvas 無原生描邊 → 8 向疊同色文字模擬

⚠️ R20：Canvas 用系統字型 vs libass PlayRes 座標系，**唔係逐像素預覽**；
疊圖只係睇大概位置同效果，要精確確認就燒錄。
"""

from __future__ import annotations

import tkinter as tk

from .. import theme


def resolved_margin_v(style: dict) -> int:
    """margin_v=0 時同 StyleConfig.resolved 一致（1080p ≈ 59px）。"""
    margin = int(style.get("margin_v") or 0)
    if margin:
        return margin
    play_res_y = int(style.get("play_res_y") or 1080)
    return max(24, round(play_res_y * 0.055))


def draw_subtitle(canvas: tk.Canvas, x0: float, y0: float, w: float, h: float,
                  line: dict | None, style: dict, cursor_ms: int) -> None:
    """喺影片畫面（x0,y0,w,h）上面畫當前行字幕疊圖。"""
    if line is None:
        return
    play_res_y = int(style.get("play_res_y") or 1080) or 1080
    scale = h / play_res_y
    size = max(8, round(int(style.get("font_size") or 48) * scale))
    margin_v = round(resolved_margin_v(style) * scale)
    outline = max(1.0, float(style.get("outline_width") or 0) or 3.0)
    outline = max(1, round(outline * scale))

    family = style.get("font_name") or theme.ui_font_family()
    words = line.get("words") or []
    text = "".join(word.get("text", "") for word in words)
    if not text:
        return

    font = (family, size)
    # 逐字時間（唱過判定用）
    chars: list[tuple[str, int]] = []     # (char, char_begin_ms)
    for word in words:
        for char in word.get("chars") or []:
            chars.append((char.get("text", ""), int(char.get("begin_ms", 0))))

    char_w = [canvas.tk.call("font", "measure", font, ch) for ch, _ in chars]
    total_w = sum(char_w)
    base_y = y0 + h - margin_v - size
    x = x0 + (w - total_w) / 2

    outline_color = style.get("outline_color") or "#000000"
    for (ch, begin_ms), cw in zip(chars, char_w):
        sung = cursor_ms >= begin_ms
        color = (style.get("primary_color") or theme.SUB_PRIMARY) if sung \
            else (style.get("secondary_color") or theme.SUB_SECONDARY)
        _draw_outlined(canvas, x, base_y, ch, font, color, outline_color, outline)
        x += cw


def _draw_outlined(canvas: tk.Canvas, x: float, y: float, ch: str,
                   font: tuple, color: str, outline_color: str, outline: int) -> None:
    """8 方向疊字模擬描邊。"""
    for dx in (-outline, 0, outline):
        for dy in (-outline, 0, outline):
            if dx or dy:
                canvas.create_text(x + dx, y + dy, text=ch, font=font,
                                   fill=outline_color, anchor="nw")
    canvas.create_text(x, y, text=ch, font=font, fill=color, anchor="nw")
