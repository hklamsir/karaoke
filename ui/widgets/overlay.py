# -*- coding: utf-8 -*-
"""overlay — 字幕疊圖渲染（計劃書 §6.7.5：大 preview 窗嘅真正價值）。

tkinter.Canvas 直接畫字（唔用 libass）：
- 字級縮放 = font_size × (畫布高 / play_res_y) —— 跟原片比例唔走位
- 底部置中（ASS Alignment=2）；margin_v 同樣按比例
- 游標之前嘅字 = primary（唱過）；之後 = secondary（未唱）
- Canvas 無原生描邊 → 8 向疊同色文字模擬

ADR-031：顯示窗同倒數圓點**一律呼叫 ``main.compute_display_windows`` /
``main.compute_countdown_dots``**，同燒錄共用同一份計算 —— 以前呢度自己抄一份
``_dual_rows``，兩邊一改就「所見非所得」，現在只有一個真源。

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


def _style_value(key: str, value: Any, fallback: Any) -> Any:
    return value if value not in (None, "") else fallback


def _layout(style: dict, h: float, y0: float) -> dict[str, Any]:
    """算出所有行位置（PlayRes → 畫布座標），單行／雙行共用。

    Returns:
        ``{"an", "size", "outline", "single_top", "up_top", "down_top"}``
    """
    size, outline, margin_v, gap = _metrics(style, h)
    an = int(style.get("alignment") or 2)
    if an not in (2, 5, 8):
        an = 2
    # 單行：跟 ASS Style 的 Alignment + MarginV
    if an == 8:
        single_top = y0 + margin_v
    elif an == 5:
        single_top = y0 + h / 2 - size / 2
    else:
        single_top = y0 + h - margin_v - size
    # 雙行：上行 = 下行 − 行距（an8 反過來由頂往下排）
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
    return {"an": an, "size": size, "outline": outline,
            "single_top": single_top, "up_top": up_top, "down_top": down_top}


def _dot_xy(center_x: float, row_top: float, layout: dict[str, Any]) -> tuple[float, float]:
    """圓點左上角：放喺目標行上方；頂部對齊時改放行下方（同 main 一致）。"""
    size = layout["size"]
    dot_size = max(6, round(size * 0.40))
    pad = max(1, round(size * 0.12))
    if layout["an"] == 8:
        return center_x, row_top + size + pad
    return center_x, row_top - pad - dot_size


def _draw_dots(canvas: tk.Canvas, center_x: float, dot_top: float, count: int,
               layout: dict[str, Any], style: dict) -> None:
    """逐點畫圓點（全用唱過色 + 描邊，跟 ASS ``\\fs`` / ``\\fsp``）。"""
    size = max(6, round(layout["size"] * 0.40))
    color = _style_value("primary_color", style.get("primary_color"), theme.SUB_PRIMARY)
    outline_color = _style_value("outline_color", style.get("outline_color"), "#000000")
    font = (style.get("font_name") or theme.ui_font_family(), size)
    gap = max(2, round(size * 0.35))
    total = size * count + gap * (count - 1)
    x = center_x - total / 2
    for _ in range(count):
        _draw_outlined(canvas, x, dot_top, "●", font, color, outline_color,
                       layout["outline"])
        x += size + gap


def draw_overlay(canvas: tk.Canvas, x0: float, y0: float, w: float, h: float,
                 lines: list[dict[str, Any]], style: dict, cursor_ms: int) -> None:
    """按 ``style`` 畫歌詞疊圖（單行／雙行）＋ 倒數圓點（ADR-031）。

    顯示窗同圓點時間全部由 ``main`` 嘅純函數提供 —— 燒錄與預覽永遠一致。

    Args:
        canvas: 目標 Canvas。
        x0, y0, w, h: 影片畫面喺畫布上嘅位置與大小。
        lines: ``timings.json`` 的 ``lines``。
        style: ``timings.json`` 的 ``style`` 段。
        cursor_ms: 目前播放位置（絕對毫秒）。
    """
    if not lines:
        return
    import main     # 延遲 import：UI ↔ CLI 共用同一份窗計算

    dual = bool(style.get("dual_line"))
    starts = [int(ln.get("start_ms", 0)) for ln in lines]
    ends = [int(ln.get("end_ms", 0)) for ln in lines]
    lead_in = int(style.get("lead_in_ms", 3000))
    windows = main.compute_display_windows(
        starts, ends, lead_in_ms=lead_in, dual_line=dual,
        delay_ms=int(style.get("switch_delay_ms") or 1000))
    dots_on = bool(style.get("countdown_dots", True))
    dot_times = (main.compute_countdown_dots(starts, windows, lead_in_ms=lead_in)
                 if dots_on else [[] for _ in lines])

    layout = _layout(style, h, y0)
    mid_x = x0 + w / 2
    up_pct = float(style.get("up_offset_pct") or 0)
    down_pct = float(style.get("down_offset_pct") or 0)

    def row_of(index: int) -> tuple[float, float]:
        """回傳 ``(center_x, top_y)``；單行固定用 ``single_top``。"""
        if not dual:
            return mid_x, layout["single_top"]
        if index % 2 == 0:
            return mid_x + w * up_pct / 100.0, layout["up_top"]
        return mid_x + w * down_pct / 100.0, layout["down_top"]

    for i, line in enumerate(lines):
        win_start, win_end = windows[i]
        if not (win_start <= cursor_ms < win_end):
            continue
        center_x, top = row_of(i)
        _draw_line(canvas, center_x, top, line, style, cursor_ms,
                   layout["size"], layout["outline"])
        # 圓點：只在**開唱前**畫（ASS 的圓點 event 也是開唱即結束）。
        # ⚠️ 唔加 `cursor_ms < starts[i]` 的話，唱完之後句尾停留期間會一直
        #    見到 3 粒圓點 -- 同成品唔一致。
        if cursor_ms < starts[i]:
            times = [t for t in dot_times[i] if t < starts[i]]
            lit = sum(1 for t in times if cursor_ms >= t)
            if lit:
                dot_x, dot_top = _dot_xy(center_x, top, layout)
                _draw_dots(canvas, dot_x, dot_top, lit, layout, style)


def _draw_outlined(canvas: tk.Canvas, x: float, y: float, ch: str,
                   font: tuple, color: str, outline_color: str, outline: int) -> None:
    """8 方向疊字模擬描邊。"""
    for dx in (-outline, 0, outline):
        for dy in (-outline, 0, outline):
            if dx or dy:
                canvas.create_text(x + dx, y + dy, text=ch, font=font,
                                   fill=outline_color, anchor="nw")
    canvas.create_text(x, y, text=ch, font=font, fill=color, anchor="nw")
