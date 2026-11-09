# -*- coding: utf-8 -*-
"""overlay — 字幕疊圖渲染（計劃書 §6.7.5：大 preview 窗嘅真正價值）。

tkinter.Canvas 直接畫字（唔用 libass）：
- 字級縮放 = font_size × (畫布高 / play_res_y) —— 跟原片比例唔走位
- 底部置中（ASS Alignment=2）；margin_v 同樣按比例
- 游標之前嘅字 = primary（唱過）；之後 = secondary（未唱）
- Canvas 無原生描邊 → 8 向疊同色文字模擬

ADR-027：``draw_dual_subtitle`` 對應雙行模式（奇數句上行、偶數句下行），
換行窗口規則同 ``main._dual_line_window`` 一致 —— 兩邊改動必須同步，
否則編輯器預覽同燒錄結果會唔同（所見非所得）。

⚠️ R20：Canvas 用系統字型 vs libass PlayRes 座標系，**唔係逐像素預覽**；
疊圖只係睇大概位置同效果，要精確確認就燒錄。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

from .. import theme


def resolved_margin_v(style: dict) -> int:
    """margin_v=0 時同 StyleConfig.resolved 一致（1080p ≈ 59px）。"""
    margin = int(style.get("margin_v") or 0)
    if margin:
        return margin
    play_res_y = int(style.get("play_res_y") or 1080)
    return max(24, round(play_res_y * 0.055))


def resolved_line_gap(style: dict) -> int:
    """雙行行距（PlayRes 座標）；同 ``StyleConfig.resolved`` 一致。"""
    gap = int(style.get("line_gap") or 0)
    if gap:
        return gap
    return max(24, round(int(style.get("font_size") or 48) * 1.45))


def _metrics(style: dict, h: float) -> tuple[int, int, int, int]:
    """回傳 ``(size, outline, margin_v, line_gap)``，全部已按畫布縮放。"""
    play_res_y = int(style.get("play_res_y") or 1080) or 1080
    scale = h / play_res_y
    size = max(8, round(int(style.get("font_size") or 48) * scale))
    margin_v = round(resolved_margin_v(style) * scale)
    outline = max(1.0, float(style.get("outline_width") or 0) or 3.0)
    outline = max(1, round(outline * scale))
    gap = max(4, round(resolved_line_gap(style) * scale))
    return size, outline, margin_v, gap


def _char_list(line: dict) -> list[tuple[str, int]]:
    """``[(char, char_begin_ms)]``，跨 words。"""
    chars: list[tuple[str, int]] = []
    for word in line.get("words") or []:
        for char in word.get("chars") or []:
            chars.append((char.get("text", ""), int(char.get("begin_ms", 0))))
    return chars


def _draw_line(canvas: tk.Canvas, center_x: float, top_y: float, line: dict,
               style: dict, cursor_ms: int, size: int, outline: int) -> None:
    """喺指定中心／頂部位置畫一行逐字變色字幕。"""
    chars = _char_list(line)
    if not chars:
        return
    family = style.get("font_name") or theme.ui_font_family()
    font = (family, size)
    char_w = [canvas.tk.call("font", "measure", font, ch) for ch, _ in chars]
    total_w = sum(char_w)
    x = center_x - total_w / 2

    outline_color = style.get("outline_color") or "#000000"
    for (ch, begin_ms), cw in zip(chars, char_w):
        sung = cursor_ms >= begin_ms
        color = (style.get("primary_color") or theme.SUB_PRIMARY) if sung \
            else (style.get("secondary_color") or theme.SUB_SECONDARY)
        _draw_outlined(canvas, x, top_y, ch, font, color, outline_color, outline)
        x += cw


def draw_subtitle(canvas: tk.Canvas, x0: float, y0: float, w: float, h: float,
                  line: dict | None, style: dict, cursor_ms: int) -> None:
    """喺影片畫面（x0,y0,w,h）上面畫當前行字幕疊圖（單行模式）。"""
    if line is None:
        return
    size, outline, margin_v, _gap = _metrics(style, h)
    if not _char_list(line):
        return
    # ADR-030：預覽亦要跟「對齊」，否則預覽底部、燒錄頂部（所見非所得）
    an = int(style.get("alignment") or 2)
    if an == 8:
        top = y0 + margin_v
    elif an == 5:
        top = y0 + h / 2 - size / 2
    else:
        top = y0 + h - margin_v - size
    _draw_line(canvas, x0 + w / 2, top, line, style, cursor_ms, size, outline)


def _dual_rows(lines: list[dict[str, Any]], delay_ms: int,
               cursor_ms: int) -> tuple[dict | None, dict | None]:
    """回傳 ``(上行句, 下行句)``；窗口規則同步 ``main._dual_line_window``。"""
    n = len(lines)
    up: dict | None = None
    down: dict | None = None
    for i, line in enumerate(lines):
        start = 0 if i < 2 else int(lines[i - 1].get("start_ms", 0)) + delay_ms
        if i + 1 < n:
            end = int(lines[i + 1].get("start_ms", 0)) + delay_ms
        else:
            end = int(line.get("end_ms", 0))
        if end <= start:
            continue
        if start <= cursor_ms < end:
            if i % 2 == 0:
                up = line
            else:
                down = line
    return up, down


def draw_dual_subtitle(canvas: tk.Canvas, x0: float, y0: float, w: float, h: float,
                       lines: list[dict[str, Any]], style: dict,
                       cursor_ms: int) -> None:
    """雙行模式疊圖（ADR-027）：上行 = 奇數句、下行 = 偶數句。

    Args:
        lines: 全部歌詞行（timings.json 的 ``lines``）。
        style: timings.json 的 ``style`` 段（含 ``switch_delay_ms`` /
            ``line_gap``；缺就按預設）。
        cursor_ms: 目前播放位置（絕對毫秒）。
    """
    if not lines:
        return
    delay_ms = max(0, int(style.get("switch_delay_ms") or 1000))
    up, down = _dual_rows(lines, delay_ms, cursor_ms)

    size, outline, margin_v, gap = _metrics(style, h)
    # ADR-030：垂直位置跟「對齊」（2 底 / 8 頂 / 5 中），水平各自錯位
    an = int(style.get("alignment") or 2)
    if an == 8:
        up_top = y0 + margin_v
        down_top = up_top + gap
    elif an == 5:
        mid = y0 + h / 2
        up_top = mid - gap / 2 - size / 2
        down_top = mid + gap / 2 - size / 2
    else:
        down_top = y0 + h - margin_v - size
        up_top = down_top - gap
    up_cx = x0 + w / 2 + w * float(style.get("up_offset_pct") or 0) / 100.0
    down_cx = x0 + w / 2 + w * float(style.get("down_offset_pct") or 0) / 100.0

    if down is not None:
        _draw_line(canvas, down_cx, down_top, down, style, cursor_ms, size, outline)
    if up is not None:
        _draw_line(canvas, up_cx, up_top, up, style, cursor_ms, size, outline)


def _draw_outlined(canvas: tk.Canvas, x: float, y: float, ch: str,
                   font: tuple, color: str, outline_color: str, outline: int) -> None:
    """8 方向疊字模擬描邊。"""
    for dx in (-outline, 0, outline):
        for dy in (-outline, 0, outline):
            if dx or dy:
                canvas.create_text(x + dx, y + dy, text=ch, font=font,
                                   fill=outline_color, anchor="nw")
    canvas.create_text(x, y, text=ch, font=font, fill=color, anchor="nw")
