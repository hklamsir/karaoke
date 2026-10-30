# -*- coding: utf-8 -*-
"""timeline — Canvas 時間軸（計劃書 §6.2；版面依據 docs/線框圖/02_編輯器.html）。

R2 對策：
- 縮放收窄到 4 級固定值（state.ZOOM_LEVELS）
- hit-test 幾何全部喺**純函數** ``compute_layout`` / ``hit_test`` 內，方便 V3 單元測試
R7 對策：拖曳期間唔 delete("all")，只 update 受影響 item；全程 redraw 用 after debounce。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

from .. import theme

#: 時間軸各段高度（§6.1：波形 44 + 刻度 18 + 字塊 + 概覽 16）
RULER_H = 18
WAVE_H = 44
LANE_H = 22
OVERVIEW_H = 16
EDGE_PX = 5          # 字塊邊界 5px 內 = 拖邊界
CHAR_MIN_W = 3       # §4.3：細過就唔畫（聚合顯示）


# ---------------------------------------------------------------------------
# 純幾何（無 Tk，V3 測試對象）
# ---------------------------------------------------------------------------

def compute_layout(lines: list[dict], px_per_ms: float, view_start_ms: int,
                   canvas_w: int, y_top: int, y_offset_px: int = 0,
                   indices: list[int] | None = None
                   ) -> list[tuple[int, int, int, float, float, float, float, str]]:
    """計算畫面內所有字塊矩形（§4.3 聚合顯示）。

    fit 縮放下逐字方塊會細過 3px（實測用戶：見唔到字塊）→ 自動降級：
    逐字 → 逐詞 → 逐行，每層塊闊度夠 3px 先用。

    Returns:
        list of (line_i, word_i, char_i, x0, y0, x1, y1, label)；
        label = 該塊要顯示嘅文字（逐字=字 / 逐詞=詞 / 逐行=整句）。
    """
    rects: list[tuple[int, int, int, float, float, float, float, str]] = []
    for row_i, line in enumerate(lines):
        line_i = row_i if indices is None else indices[row_i]
        y0 = y_top + row_i * LANE_H - y_offset_px
        words = list(enumerate(line.get("words") or []))
        flat = [(wi, ci, c) for wi, w in words for ci, c in enumerate(w.get("chars") or [])]
        if not flat:
            continue
        lx0 = (int(line.get("start_ms", 0)) - view_start_ms) * px_per_ms
        lx1 = (int(line.get("end_ms", 0)) - view_start_ms) * px_per_ms
        if lx1 < -CHAR_MIN_W or lx0 > canvas_w + CHAR_MIN_W:
            continue

        def _rect(b_ms: int, e_ms: int, ref: tuple[int, int, int], label: str) -> None:
            x0 = (b_ms - view_start_ms) * px_per_ms
            x1 = (e_ms - view_start_ms) * px_per_ms
            if x1 < -CHAR_MIN_W or x0 > canvas_w + CHAR_MIN_W:
                return
            rects.append((ref[0], ref[1], ref[2], x0, y0, max(x0 + 1.0, x1),
                          y0 + LANE_H - 4, label))

        char_px = (lx1 - lx0) / max(1, len(flat))
        if char_px >= CHAR_MIN_W:
            # 逐字模式（原行為）
            for wi, ci, c in flat:
                _rect(int(c.get("begin_ms", 0)), int(c.get("end_ms", 0)),
                      (line_i, wi, ci), c.get("text", ""))
            continue
        word_px = (lx1 - lx0) / max(1, len(words))
        if word_px >= CHAR_MIN_W:
            # 逐詞模式
            for wi, w in words:
                _rect(int(w.get("begin_ms", 0)), int(w.get("end_ms", 0)),
                      (line_i, wi, 0), w.get("text", ""))
            continue
        # 逐行模式（fit 縮放）
        _rect(int(line.get("start_ms", 0)), int(line.get("end_ms", 0)),
              (line_i, 0, 0), line.get("text", ""))
    return rects


def hit_test(rects: list[tuple], x: float, y: float, edge_px: int = EDGE_PX
             ) -> tuple[str, tuple[int, int, int] | None]:
    """(x, y) → (mode, (line_i, word_i, char_i))；mode ∈ body/left/right/empty。

    V3 測試對象：字塊內 → body；字塊邊界 edge_px 內 → left/right；空白 → empty。
    """
    for r in reversed(rects):
        line_i, word_i, char_i, x0, y0, x1, y1 = r[:7]
        if y0 <= y <= y1 and x0 - edge_px <= x <= x1 + edge_px:
            if x - x0 <= edge_px and x1 - x0 > edge_px * 2:
                return "left", (line_i, word_i, char_i)
            if x1 - x <= edge_px and x1 - x0 > edge_px * 2:
                return "right", (line_i, word_i, char_i)
            return "body", (line_i, word_i, char_i)
    return "empty", None


def ms_at_x(x: float, px_per_ms: float, view_start_ms: int) -> int:
    return int(view_start_ms + x / px_per_ms)


def nice_tick_interval(px_per_ms: float, canvas_w: int) -> int:
    """揀一個靚嘅刻度間隔（ms）：目標每 80px 一格。"""
    target_ms = 80_000 / max(px_per_ms, 1e-9)
    for step in (100, 250, 500, 1000, 2500, 5000, 10_000, 30_000, 60_000, 300_000):
        if step >= target_ms:
            return step
    return 600_000


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

class TimelineCanvas(tk.Canvas):
    """時間軸：刻度 + 逐字方塊 + 播放頭 + 全片概覽（波形背景由 commit 6 疊加）。"""

    def __init__(self, master, state, *,
                 on_seek: Callable[[int], None] | None = None,
                 on_drag_char: Callable[[], None] | None = None,
                 on_range_select: Callable[[int, int], None] | None = None) -> None:
        super().__init__(master, bg=theme.BG_DEEP, highlightthickness=1,
                         highlightbackground=theme.BORDER)
        self._state = state
        self._on_seek = on_seek
        self._on_drag_done = on_drag_char
        self._on_range_select = on_range_select
        self._rects: list[tuple] = []
        self._drag: dict[str, Any] | None = None
        self._redraw_pending = False
        self._peaks = None            # np.ndarray（commit 6 波形）
        self._peaks_duration_ms = 0

        state.add_observer(self._on_state_change)
        self.bind("<Configure>", lambda _e: self._request_redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Shift-MouseWheel>", self._on_wheel)
        self._redraw()

    # ------------------------------------------------------------------
    # 狀態同步
    # ------------------------------------------------------------------

    def _on_state_change(self, reason: str) -> None:
        if reason in ("select", "follow"):
            # lane 堆疊超出可視高度（24 行 × 22px）→ 自動捲動到當前行，
            # 唔係嘅話去到後面嘅句會「見唔到字塊」（用戶實測）
            self._scroll_to_line(self._state.current_line_i)
        self._request_redraw()

    def _scroll_to_line(self, line_i: int) -> None:
        """垂直捲動令當前行 lane 入鏡——**已經入鏡就唔捲**。

        ⚠️ content_h 必須同 _redraw_now 嘅 scrollregion 一致（要計 y_top），
        唔係嘅話 fraction 偏大，跟播會愈捲愈上直至 lane 消失（用戶實測）。
        """
        lines = self._state.lines
        if not lines:
            return
        y_top = self.waveform_y_top() + 2
        if self._peaks is not None and len(self._peaks) > 0:
            y_top += WAVE_H + 4
        content_h = y_top + max(1, len(lines)) * LANE_H
        lane_y = y_top + max(0, line_i) * LANE_H
        view_top = self.yview()[0] * content_h
        view_h = max(1, self.winfo_height() - OVERVIEW_H - y_top)
        # 已入鏡（上下各留一行 buffer）就唔好郁
        if view_top + LANE_H <= lane_y <= view_top + view_h - LANE_H:
            return
        top = max(0.0, min(content_h - view_h, lane_y - y_top - LANE_H * 2))
        self.yview_moveto(top / content_h)

    def set_waveform(self, peaks, duration_ms: int) -> None:
        """音訊載入完成後畀時間軸畫波形背景（§6.6）。"""
        self._peaks = peaks
        self._peaks_duration_ms = duration_ms
        self._request_redraw()

    def waveform_y_top(self) -> int:
        return RULER_H + 2

    def _request_redraw(self) -> None:
        """R7：拖曳連續重繪用 after debounce（~16ms 一幀）。"""
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after(16, self._redraw_now)

    # ------------------------------------------------------------------
    # 繪製
    # ------------------------------------------------------------------

    def _redraw(self) -> None:
        self._redraw_now()

    def _redraw_now(self) -> None:
        self._redraw_pending = False
        self.delete("all")
        st = self._state
        w = max(self.winfo_width(), 400)
        ppm = st.px_per_ms
        view = st.view_start_ms
        lines = st.lines

        # --- 刻度 ---
        interval = nice_tick_interval(ppm, w)
        first = (view // interval) * interval
        t = first
        while True:
            x = (t - view) * ppm
            if x > w:
                break
            if x >= 0:
                sec_total = t // 1000
                label = f"{sec_total // 60}:{sec_total % 60:02d}"
                if interval >= 60_000:
                    label = f"{sec_total // 60}m"
                self.create_line(x, 0, x, RULER_H, fill=theme.BORDER)
                self.create_text(x + 3, 1, text=label, anchor="nw",
                                 fill=theme.FG_DIM, font=(theme.mono_font_family(), 8))
            t += interval

        # --- 波形（§6.6：filled polygon；載入音訊後先有）---
        y_top = RULER_H + 2
        if self._peaks is not None and len(self._peaks) > 0:
            wy0, wy1 = y_top, y_top + WAVE_H
            ms_per_bin = self._peaks_duration_ms / len(self._peaks)
            # 每 px 想畫 1 點：bins per px = 1 / (ms_per_bin * ppm)
            step = max(1, int(round(1.0 / (ms_per_bin * ppm))))
            pts_top: list[tuple[float, float]] = []
            pts_bot: list[tuple[float, float]] = []
            for i in range(0, len(self._peaks), step):
                x = (i * ms_per_bin - view) * ppm
                if x < -2:
                    continue
                if x > w + 2:
                    break
                h = max(0.5, float(self._peaks[i]) * (WAVE_H / 2))
                pts_top.append((x, (wy0 + wy1) / 2 - h))
                pts_bot.append((x, (wy0 + wy1) / 2 + h))
            if len(pts_top) >= 2:
                poly = pts_top + pts_bot[::-1]
                self.create_polygon(poly, fill="#2a3a52", outline="", smooth=True)
                self.create_line(pts_top, fill="#3AA0FF", width=1)
            y_top = wy1 + 4

        # --- 逐字方塊（每行一條 lane；篩選模式只畫有估算字嘅行 §4.4）---
        visible = st.visible_lines()
        indices = [i for i, _ in visible]
        view_lines = [l for _, l in visible]
        self._rects = compute_layout(view_lines, ppm, view, w, y_top, indices=indices)
        current_line = st.current_line_i
        selected = st.selected
        for line_i, word_i, char_i, x0, y0, x1, y1, label in self._rects:
            word = st.lines[line_i]["words"][word_i]
            estimated = bool(word.get("estimated"))
            is_sel = selected == (line_i, word_i, char_i)
            # 配色（用戶回報：普通字塊太暗以為冇用）—— 藍灰＝已確認，暗橙＝估算
            if estimated:
                fill = "#4a3c22"
            else:
                fill = "#3b4a63" if line_i != current_line else "#4a5d80"
            outline = theme.WARNING if estimated else "#5a6d8c"
            width = 1
            if is_sel:
                fill, outline, width = theme.ACCENT_DIM, theme.ACCENT, 2
            self.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline,
                                  width=width,
                                  dash=(2, 2) if estimated else ())
            # 文字：夠寬先畫（聚合模式 label = 詞/整句）
            if x1 - x0 >= 14:
                self.create_text((x0 + x1) / 2, (y0 + y1) / 2,
                                 text=label,
                                 fill=theme.FG_MAIN, font=(theme.ui_font_family(), 10))

        # --- 無字幕區段斜紋（R5）：>3 秒冇行覆蓋嘅 gap ---
        lane_bottom = y_top + max(len(view_lines), 1) * LANE_H
        self._draw_gaps(view, ppm, w, y_top, lane_bottom)

        # --- 播放頭 ---
        cx = (st.cursor_ms - view) * ppm
        if 0 <= cx <= w:
            self.create_line(cx, 0, cx, self.winfo_height() - OVERVIEW_H,
                             fill=theme.ACCENT, width=2)
            self.create_polygon(cx - 4, 0, cx + 4, 0, cx, 6, fill=theme.ACCENT, outline="")

        # --- 全片概覽（§6.2：藍=視窗）---
        total = max(1, models_total_ms(st))
        oy = self.winfo_height() - OVERVIEW_H
        if oy > y_top:
            self.create_rectangle(0, oy, w, oy + OVERVIEW_H, fill="#101016",
                                  outline=theme.BORDER)
            vw = max(8, w * (w / ppm) / total)
            vx = w * view / total
            self.create_rectangle(vx, oy + 1, min(w, vx + vw), oy + OVERVIEW_H - 1,
                                  fill=theme.ACCENT_DIM, outline="")
            # 無字幕區段：有歌詞覆蓋嘅時間畫淡色（R5 嘅反面）
            for line in lines:
                x0 = int(line.get("start_ms", 0)) / total * w
                x1 = int(line.get("end_ms", 0)) / total * w
                self.create_rectangle(x0, oy + OVERVIEW_H - 4, max(x1, x0 + 1),
                                      oy + OVERVIEW_H - 2, fill="#5a5a6e", outline="")
            self._overview_y = oy   # 畀 hit-test 知概覽條範圍（可㩒可拖嚟平移）

        self.configure(scrollregion=(0, 0, w, max(self.winfo_height(),
                                                  y_top + max(len(view_lines), 1) * LANE_H)))

    def _draw_gaps(self, view: int, ppm: float, w: int, y_top: int, y_bottom: int) -> None:
        """無字幕區段斜紋（R5）：>3 秒冇行覆蓋嘅時間段畫灰斜紋。"""
        st = self._state
        lines = st.lines
        if not lines or y_bottom <= y_top:
            return
        spans = sorted((int(l.get("start_ms", 0)), int(l.get("end_ms", 0))) for l in lines)
        gaps: list[tuple[int, int]] = []
        prev_end = 0
        for start, end in spans:
            if start - prev_end > 3000:
                gaps.append((prev_end, start))
            prev_end = max(prev_end, end)
        for g0, g1 in gaps:
            x0 = (g0 - view) * ppm
            x1 = (g1 - view) * ppm
            if x1 < 0 or x0 > w:
                continue
            x0, x1 = max(0, x0), min(w, x1)
            if x1 - x0 < 6:
                continue
            self.create_rectangle(x0, y_top, x1, y_bottom, fill="#1c1c24",
                                  outline=theme.BORDER, stipple="gray50")
            self.create_line(x0, y_bottom, x1, y_top, fill="#2c2c38")
            if x1 - x0 > 90:
                self.create_text((x0 + x1) / 2, (y_top + y_bottom) / 2,
                                 text="無字幕區段", fill=theme.FG_DIM,
                                 font=(theme.ui_font_family(), 9))

    # ------------------------------------------------------------------
    # 互動
    # ------------------------------------------------------------------

    def _on_press(self, event) -> None:
        st = self._state
        # 概覽條（底部藍色）＝可㩒／拖曳平移（用戶回報：放大後卷軸移唔返左）
        if event.y >= getattr(self, "_overview_y", 10 ** 9):
            self._drag = {"mode": "oview"}
            self._center_view_at(event.x)
            return
        mode, ref = hit_test(self._rects, event.x, event.y)
        st = self._state
        if mode == "empty":
            ms = ms_at_x(event.x, st.px_per_ms, st.view_start_ms)
            st.set_cursor(ms)
            if self._on_seek:
                self._on_seek(ms)
            self._drag = {"mode": "scrub", "x": event.x}
            return
        line_i, word_i, char_i = ref
        st.set_selection(ref)
        line = st.lines[line_i]
        word = line["words"][word_i]
        char = word["chars"][char_i]
        self._drag = {
            "mode": mode, "ref": ref, "x": event.x,
            "orig_char": (int(char["begin_ms"]), int(char["end_ms"])),
            "orig_word_chars": [(int(c["begin_ms"]), int(c["end_ms"])) for c in word["chars"]],
        }

    def _center_view_at(self, x: float) -> None:
        """概覽條互動：將視窗中心移去 x 對應嘅時間（用戶需求 #2）。"""
        st = self._state
        w = max(self.winfo_width(), 100)
        total = max(1, models_total_ms(st))
        ms = x / w * total
        view_span = w / st.px_per_ms
        st.view_start_ms = int(max(0, min(ms - view_span / 2, max(0, total - view_span))))
        st.notify("pan")

    def _on_motion(self, event) -> None:
        drag = self._drag
        if not drag:
            return
        st = self._state
        ppm = st.px_per_ms
        if drag["mode"] == "oview":
            self._center_view_at(event.x)
            return
        if drag["mode"] == "scrub":
            ms = ms_at_x(event.x, ppm, st.view_start_ms)
            st.set_cursor(ms)
            if self._on_seek:
                self._on_seek(ms)
            return
        if drag["mode"] == "range":
            st.set_cursor(ms_at_x(event.x, ppm, st.view_start_ms))
            return
        delta_ms = int((event.x - drag["x"]) / ppm)
        line_i, word_i, char_i = drag["ref"]
        line = st.lines[line_i]
        word = line["words"][word_i]
        char = word["chars"][char_i]
        if drag["mode"] == "body":
            # C3：拖 word 本體 = 平移 word 內所有 char
            for c, (b, e) in zip(word["chars"], drag["orig_word_chars"]):
                c["begin_ms"], c["end_ms"] = b + delta_ms, e + delta_ms
            word["begin_ms"], word["end_ms"] = word["chars"][0]["begin_ms"], word["chars"][-1]["end_ms"]
        elif drag["mode"] == "left":
            b0, e0 = drag["orig_char"]
            char["begin_ms"] = min(max(0, b0 + delta_ms), e0 - 1)
        elif drag["mode"] == "right":
            b0, e0 = drag["orig_char"]
            char["end_ms"] = max(e0 + delta_ms, b0 + 1)
        word["begin_ms"], word["end_ms"] = word["chars"][0]["begin_ms"], word["chars"][-1]["end_ms"]
        # 拖曳中已經算改過 → 清 estimated（最終由 command N7 收尾）
        word["estimated"] = False
        self._request_redraw()

    def _on_release(self, event) -> None:
        drag = self._drag
        self._drag = None
        if not drag:
            return
        st = self._state
        if drag["mode"] in ("body", "left", "right"):
            line_i, word_i, char_i = drag["ref"]
            char = st.lines[line_i]["words"][word_i]["chars"][char_i]
            new = (int(char["begin_ms"]), int(char["end_ms"]))
            if drag["mode"] == "body":
                # command 記 word 級平移
                from ..commands import SetWordShift
                cmd = SetWordShift(line_i, word_i, new[0] - drag["orig_char"][0],
                                   drag["orig_word_chars"])
                # SetWordShift.apply 會由 originals 重算 —— 先還原再 push
                for c, (b, e) in zip(st.lines[line_i]["words"][word_i]["chars"],
                                     drag["orig_word_chars"]):
                    c["begin_ms"], c["end_ms"] = b, e
                st.lines[line_i]["words"][word_i]["begin_ms"] = \
                    st.lines[line_i]["words"][word_i]["chars"][0]["begin_ms"]
                st.lines[line_i]["words"][word_i]["end_ms"] = \
                    st.lines[line_i]["words"][word_i]["chars"][-1]["end_ms"]
                st.push(cmd)
            else:
                from ..commands import SetCharTiming
                old = drag["orig_char"]
                if new != old:
                    st.push(SetCharTiming(line_i, word_i, char_i, old, new))
                else:
                    st.notify("edit")
            if self._on_drag_done:
                self._on_drag_done()

    def _on_double(self, event) -> None:
        mode, ref = hit_test(self._rects, event.x, event.y)
        if mode in ("body", "left", "right") and self._on_range_select is None:
            pass   # 雙擊改字由 editor page 嘅 on_char_edit hook 處理（commit 8）

    def _on_wheel(self, event) -> None:
        st = self._state
        if event.state & 0x0001:      # Shift = 水平捲動
            delta = -int(event.delta / 120) * 200 / st.px_per_ms
            st.view_start_ms = max(0, int(st.view_start_ms + delta))
            st.notify("pan")
        else:
            anchor = ms_at_x(event.x if event.x > 0 else self.winfo_width() / 2,
                             st.px_per_ms, st.view_start_ms)
            st.zoom(1 if event.delta > 0 else -1, anchor_ms=anchor)


def models_total_ms(state) -> int:
    from ..models import total_duration_ms
    return max(1, total_duration_ms(state.doc))
