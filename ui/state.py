# -*- coding: utf-8 -*-
"""state — EditorState：單一數據源（R22 對策）。

三處顯示（歌詞樹 / 當前字幕卡 / 逐字微調）全部由呢度推導，**唔做雙份 state**；
更新經 ``notify()`` 廣播畀 observer（widget 註冊 callback）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import commands, models

#: 縮放層級（用戶需求 #2：加到 6 級）：px per second
ZOOM_LEVELS = [0.25, 1.0, 4.0, 16.0, 32.0, 64.0]


class EditorState:
    """文檔 + 選取 + 游標 + 髒標記 + undo stack。"""

    def __init__(self) -> None:
        self.observers: list[Callable[[str], None]] = []
        self.undo_stack = commands.UndoStack()
        self.doc: dict[str, Any] = {"source": {}, "style": {}, "lines": []}
        self.path: Path | None = None
        self.dirty = False
        self.current_line_i = 0
        self.cursor_ms = 0
        self.zoom_idx = 1
        #: 動態縮放層（開檔時 zoom_fit 會插入「成條片啱啱好塞滿畫布」嘅層級）
        self.zoom_levels: list[float] = list(ZOOM_LEVELS)
        self.view_start_ms = 0
        #: 選取 = (line_i, word_i, char_i) | None
        self.selected: tuple[int, int, int] | None = None
        #: 選中嘅 word（斷句掣用）= (line_i, word_i) | None
        self.selected_word: tuple[int, int] | None = None
        #: 「只顯示估算字」篩選（§4.4）：歌詞樹 + 時間軸都只列有估算字嘅行
        self.filter_estimated = False

    # ------------------------------------------------------------------
    # observer
    # ------------------------------------------------------------------

    def add_observer(self, fn: Callable[[str], None]) -> None:
        self.observers.append(fn)

    def notify(self, reason: str = "") -> None:
        for fn in list(self.observers):
            fn(reason)

    # ------------------------------------------------------------------
    # 開檔 / 存檔
    # ------------------------------------------------------------------

    def load(self, path: str | Path) -> None:
        """讀 timings.json（§5.3）：v1/v2/v3 相容；原片唔存在時保留路徑畀 UI 提示。"""
        self.doc = models.load_doc(path)
        self.path = Path(path)
        self.dirty = False
        self.undo_stack.clear()
        self.current_line_i = 0
        self.cursor_ms = 0
        self.zoom_idx = 1
        self.zoom_levels = list(ZOOM_LEVELS)
        self.view_start_ms = 0
        self.selected = None
        self.selected_word = None
        self._refresh_texts()   # 舊檔 line.text 可能有幽靈空格，開檔即以 words 重建
        self.notify("load")

    def save(self) -> None:
        """手動 Ctrl+S（E2）：normalize N1–N7 → atomic write + .bak。"""
        if self.path is None:
            raise ValueError("冇檔案路徑")
        models.normalize(self.doc)
        models.atomic_write_json(self.path, self.doc)
        self.dirty = False
        self.notify("save")

    # ------------------------------------------------------------------
    # undo / redo
    # ------------------------------------------------------------------

    def push(self, cmd: commands.Command) -> None:
        self.undo_stack.push(cmd, self)
        self._refresh_texts()
        self.dirty = True
        self.notify("edit")

    def undo(self) -> bool:
        if self.undo_stack.undo(self) is not None:
            self._refresh_texts()
            self.dirty = True
            self.notify("edit")
            return True
        return False

    def redo(self) -> bool:
        if self.undo_stack.redo(self) is not None:
            self._refresh_texts()
            self.dirty = True
            self.notify("edit")
            return True
        return False

    def _refresh_texts(self) -> None:
        """N1 輕量版：結構操作（斷句/合併/改字）後即刻重建 ``lines[].text``。

        唔做嘅話歌詞樹顯示嘅係**舊 cached text**，用戶會以為操作冇生效
        （實測：斷句新增嗰行顯示空白）。完整 N1–N7（時間 clip 等）留返 save()。
        """
        import re
        for line in self.lines:
            # 詞文本以 chars 為準重建（改字重建嗰陣空格可能藏咗入 word text 中間，
            # strip() 清唔到 —— 用戶兩次回報歌詞樹幽靈空格）
            for word in line.get("words") or []:
                chars = word.get("chars") or []
                if chars:
                    word["text"] = "".join(c.get("text", "") for c in chars)
            line["text"] = models.display_text(line)

    # ------------------------------------------------------------------
    # 導航 / 選取
    # ------------------------------------------------------------------

    @property
    def lines(self) -> list[dict]:
        return self.doc.get("lines") or []

    def clamp_line_i(self, i: int) -> int:
        return max(0, min(i, len(self.lines) - 1))

    def set_current_line(self, i: int) -> None:
        i = self.clamp_line_i(i)
        if i == self.current_line_i:
            return   # 冪等：Treeview selection_set 會排隊觸發事件，冇變就唔好 notify（防事件循環）
        self.current_line_i = i
        self.notify("select")

    def set_selection(self, sel: tuple[int, int, int] | None) -> None:
        if sel == self.selected:
            return   # 冪等：同上，防 <<TreeviewSelect>> 排隊事件造成無限循環（實測 freeze）
        self.selected = sel
        if sel is not None:
            self.current_line_i = self.clamp_line_i(sel[0])
            self.selected_word = (sel[0], sel[1])
        self.notify("select")

    def line_at(self, ms: int) -> int | None:
        """游標所在行（start ≤ ms < end）；唔喺任何行內就最近嘅一行。"""
        lines = self.lines
        if not lines:
            return None
        for i, line in enumerate(lines):
            if int(line.get("start_ms", 0)) <= ms < int(line.get("end_ms", 0)):
                return i
        return min(range(len(lines)),
                   key=lambda i: min(abs(int(lines[i].get("start_ms", 0)) - ms),
                                     abs(int(lines[i].get("end_ms", 0)) - ms)))

    def set_cursor(self, ms: int) -> None:
        self.cursor_ms = max(0, ms)
        # 跟播高亮（用戶需求 #2）：游標行變咗就通知歌詞樹跟
        cur = self.line_at(self.cursor_ms)
        followed = False
        if cur is not None and cur != self.current_line_i:
            self.current_line_i = cur
            followed = True
        self.notify("cursor")
        if followed:
            self.notify("follow")

    @property
    def px_per_ms(self) -> float:
        return self.zoom_levels[self.zoom_idx] / 1000.0

    def zoom_fit(self, canvas_w: int) -> None:
        """開檔時：插入「成條片啱啱好塞滿畫布」嘅縮放層並套用（用戶需求 #3）。

        時間軸預設顯示 0 至片尾，唔會有大截空白。
        """
        total = models.total_duration_ms(self.doc)
        if total <= 0 or canvas_w <= 50:
            return
        # px per ms → 轉做 px/s（zoom_levels 嘅單位）：3 小時片都 fit 得入 → 下限 0.05 px/s
        fit_pps = max(0.05, min(64.0, (canvas_w - 60) / total * 1000))
        fit_pps = round(fit_pps, 4)
        self.zoom_levels = sorted(set(ZOOM_LEVELS) | {fit_pps})
        self.zoom_idx = self.zoom_levels.index(fit_pps)
        self.view_start_ms = 0
        self.notify("zoom")

    def zoom(self, direction: int, anchor_ms: int | None = None) -> None:
        """以游標為中心縮放（§6.2；工具列 ＋/－ 掣都用呢個）。"""
        anchor = anchor_ms if anchor_ms is not None else self.cursor_ms
        new_idx = max(0, min(len(self.zoom_levels) - 1, self.zoom_idx + direction))
        if new_idx == self.zoom_idx:
            return
        old_ppm = self.px_per_ms
        self.zoom_idx = new_idx
        new_ppm = self.px_per_ms
        # 保持 anchor 喺畫面同一位置
        self.view_start_ms = int(max(0, anchor - (anchor - self.view_start_ms)
                                     * (old_ppm / new_ppm)))
        self.notify("zoom")

    def visible_lines(self) -> list[tuple[int, dict]]:
        """(原始索引, 行) 列表；篩選開啟時只返有估算字嘅行（§4.4）。"""
        lines = self.lines
        if not self.filter_estimated:
            return list(enumerate(lines))
        return [(i, l) for i, l in enumerate(lines) if models.has_estimated(l)]

    def toggle_filter_estimated(self) -> None:
        self.filter_estimated = not self.filter_estimated
        self.notify("filter")

    def stats(self) -> dict[str, Any]:
        return models.doc_stats(self.doc)

    @property
    def video_path(self) -> Path | None:
        """原片路徑（§5.3：驗證存在；唔存在 → UI 提示重新指定）。"""
        video = (self.doc.get("source") or {}).get("video")
        return Path(video) if video else None

    def reassign_video(self, path: str | Path) -> None:
        """「重新指定原片」（R8）：改 source.video（燒錄要用）。"""
        self.doc.setdefault("source", {})["video"] = str(path)
        self.dirty = True
        self.notify("edit")
