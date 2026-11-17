# -*- coding: utf-8 -*-
"""overlay — 字幕疊圖渲染（計劃書 §6.7.5：大 preview 窗嘅真正價值）。

tkinter.Canvas 直接畫字（唔用 libass）：
- 字級縮放 = font_size × (畫布高 / play_res_y) —— 跟原片比例唔走位
- ⚠️ 字級單位：Tk 的**正數 size 係「點」**（96pt → 128px @96dpi），libass 的
  ``Fontsize`` 係**像素**。所以一律用 :func:`pixel_font`（負 size）渲染，
  否則預覽會比燒錄大 4/3（2026-11-11 用戶回報 #3，實測 96pt→128px vs 96px）
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
import tkinter.font as tkfont
from typing import Any

from .. import theme

#: 像素字級快取（root, family, px）→ tkfont.Font。
#: ⚠️ Tk 字級**正數 = 點**（96 → 128px @96dpi），**負數 = 像素**；libass 的
#: ``Fontsize`` 係 PlayRes 像素。用正數會令預覽字大 4/3（用戶回報 #3）。
_FONT_CACHE: dict[tuple[int, str, int], tkfont.Font] = {}


def pixel_font(family: str, px: int) -> tkfont.Font:
    """取一個「像素」單位的 Tk 字型（負 size），同 libass 對齊。

    Cache key 包埋 default root 的 id —— 測試 / 重開視窗會 destroy 舊 root，
    舊 Font 物件會即刻失效（TclError: font doesn't exist）。
    """
    px = max(1, int(px))
    root = tk._default_root
    key = (id(root), family, px)
    font = _FONT_CACHE.get(key)
    if font is None:
        if len(_FONT_CACHE) > 64:      # 換片 / 縮放拉動會累積，設上限防漏
            _FONT_CACHE.clear()
        try:
            font = tkfont.Font(root=root, family=family, size=-px)
        except tk.TclError:            # 舊 root 已 destroy → 清 cache 再做一次
            _FONT_CACHE.clear()
            font = tkfont.Font(family=family, size=-px)
        _FONT_CACHE[key] = font
    return font


def resolved_margin_v(style: dict) -> int:
    """margin_v=0 時同 StyleConfig.resolved 一致（1080p ≈ 59px）。"""
    margin = int(style.get("margin_v") or 0)
    if margin:
        return margin
    play_res_y = int(style.get("play_res_y") or 1080)
    return max(24, round(play_res_y * 0.055))


def resolved_line_gap(style: dict) -> int:
    """雙行行距（PlayRes 座標）；同 ``StyleConfig.resolved`` 一致。

    優先序（ADR-039）：``line_gap``（px）> ``line_gap_ratio``（倍數）> 1.45 舊公式。
    """
    gap = int(style.get("line_gap") or 0)
    if gap:
        return gap
    font_size = int(style.get("font_size") or 48)
    try:
        ratio = float(style.get("line_gap_ratio") or 0)
    except (TypeError, ValueError):
        ratio = 0.0
    if ratio > 0:
        return max(1, round(font_size * ratio))
    return max(24, round(font_size * 1.45))


def blend_over(fg: str, bg: str, opacity: int) -> str:
    """把 ``fg`` 按 ``opacity``（0–100）疊在 ``bg`` 上，回傳 ``#RRGGBB``。

    ADR-050：Tk Canvas 的文字**冇 alpha 通道**，半透明陰影只能預先混色。混色
    基準固定用 ``theme.VIDEO_BG``（主頁預覽的純色底），而編輯頁疊在真片段上時
    只有近似效果 —— 準確度仍以燒錄成品為準（ADR-038）。

    Args:
        fg: 前景色 ``#RRGGBB``（陰影色）。
        bg: 背景色 ``#RRGGBB``（假定底）。
        opacity: 0–100；0 = 完全透明（回傳 bg）。

    Returns:
        混色後的 ``#RRGGBB``；任一輸入不合法時回傳 ``bg``。
    """
    def _parse(value: str) -> tuple[int, int, int] | None:
        raw = (value or "").strip().lstrip("#")
        if len(raw) != 6:
            return None
        try:
            return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)
        except ValueError:
            return None

    f, b = _parse(fg), _parse(bg)
    if f is None or b is None:
        return bg if b is None else f"#{bg.lstrip('#').upper()}"
    alpha = max(0.0, min(100.0, float(opacity))) / 100.0
    mixed = tuple(round(fc * alpha + bc * (1 - alpha)) for fc, bc in zip(f, b))
    return "#{:02X}{:02X}{:02X}".format(*mixed)


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
               style: dict, cursor_ms: int, size: int, outline: int,
               shadow: float = 0.0, shadow_color: str = "") -> float:
    """喺指定中心／頂部位置畫一行逐字變色字幕。

    Returns:
        文字左邊的畫布 x（圓點要靠它對齊第一個字）。
    """
    chars = _char_list(line)
    if not chars:
        return center_x
    family = style.get("font_name") or theme.ui_font_family()
    font = pixel_font(family, size)
    char_w = [font.measure(ch) for ch, _ in chars]
    total_w = sum(char_w)
    x = center_x - total_w / 2
    left = x

    outline_color = style.get("outline_color") or "#000000"
    for (ch, begin_ms), cw in zip(chars, char_w):
        sung = cursor_ms >= begin_ms
        color = (style.get("primary_color") or theme.SUB_PRIMARY) if sung \
            else (style.get("secondary_color") or theme.SUB_SECONDARY)
        _draw_outlined(canvas, x, top_y, ch, font, color, outline_color, outline,
                       shadow, shadow_color)
        x += cw
    return left


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
    # ADR-050：陰影（PlayRes px → 畫布 px）。0 = 關；> 0 最少畫 1px，
    # 否則 720p 縮到預覽尺寸時 1px 陰影會四捨五入成 0 而「睇落冇開」。
    play_res_y = int(style.get("play_res_y") or 1080) or 1080
    shadow_raw = float(style.get("shadow_width") or 0)
    shadow = max(1, round(shadow_raw * h / play_res_y)) if shadow_raw > 0 else 0
    shadow_color = blend_over(style.get("shadow_color") or "#000000", theme.VIDEO_BG,
                              int(style.get("shadow_opacity", 50)))
    return {"an": an, "size": size, "outline": outline,
            "single_top": single_top, "up_top": up_top, "down_top": down_top,
            "shadow": shadow, "shadow_color": shadow_color}


def _dot_xy(left_x: float, row_top: float, layout: dict[str, Any]) -> tuple[float, float]:
    """圓點左上角：左邊對齊歌詞第一個字；頂部對齊時改放行下方。"""
    size = layout["size"]
    dot_size = max(6, round(size / 3))
    pad = max(1, round(size * 0.18))
    if layout["an"] == 8:
        return left_x, row_top + size + pad
    return left_x, row_top - pad - dot_size


def _draw_dots(canvas: tk.Canvas, left_x: float, dot_top: float, count: int,
               layout: dict[str, Any], style: dict) -> None:
    """由左向右逐點畫圓點（第一點永遠喺同一位置，唔會隨點數左右跳）。"""
    size = max(6, round(layout["size"] / 3))
    color = _style_value("primary_color", style.get("primary_color"), theme.SUB_PRIMARY)
    outline_color = _style_value("outline_color", style.get("outline_color"), "#000000")
    font = pixel_font(style.get("font_name") or theme.ui_font_family(), size)
    advance = size + max(0, round(layout["size"] * 0.05))
    x = left_x
    for _ in range(count):
        _draw_outlined(canvas, x, dot_top, "●", font, color, outline_color,
                       layout["outline"], layout.get("shadow", 0.0),
                       layout.get("shadow_color", ""))
        x += advance


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
    dot_times = (main.compute_countdown_dots(
        starts, windows, lead_in_ms=lead_in, ends=ends,
        min_gap_ms=int(style.get("dot_min_gap_ms", 5000) or 0))
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
        left_x = _draw_line(canvas, center_x, top, line, style, cursor_ms,
                            layout["size"], layout["outline"],
                            layout.get("shadow", 0.0), layout.get("shadow_color", ""))
        # 圓點：只在**開唱前**畫（ASS 的圓點 event 也是開唱即結束）。
        # ⚠️ 唔加 `cursor_ms < starts[i]` 的話，唱完之後句尾停留期間會一直
        #    見到 3 粒圓點 -- 同成品唔一致。
        if cursor_ms < starts[i]:
            times = [t for t in dot_times[i] if t < starts[i]]
            lit = sum(1 for t in times if cursor_ms >= t)
            if lit:
                dot_x, dot_top = _dot_xy(left_x, top, layout)
                _draw_dots(canvas, dot_x, dot_top, lit, layout, style)


def _draw_outlined(canvas: tk.Canvas, x: float, y: float, ch: str,
                   font: tkfont.Font, color: str, outline_color: str, outline: int,
                   shadow: float = 0.0, shadow_color: str = "") -> None:
    """8 方向疊字模擬描邊；有陰影就先畫右下位移的陰影字（ADR-050）。"""
    if shadow > 0 and shadow_color:
        canvas.create_text(x + shadow, y + shadow, text=ch, font=font,
                           fill=shadow_color, anchor="nw")
    for dx in (-outline, 0, outline):
        for dy in (-outline, 0, outline):
            if dx or dy:
                canvas.create_text(x + dx, y + dy, text=ch, font=font,
                                   fill=outline_color, anchor="nw")
    canvas.create_text(x, y, text=ch, font=font, fill=color, anchor="nw")
