# -*- coding: utf-8 -*-
"""state — EditorState：單一數據源（R22 對策）。

三處顯示（歌詞樹 / 當前字幕卡 / 逐字微調）全部由呢度推導，**唔做雙份 state**；
更新經 ``notify()`` 廣播畀 observer（widget 註冊 callback）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import commands, models

#: 縮放 4 級（R2 對策：唔做連續 zoom）：px per second
ZOOM_LEVELS = [0.25, 1.0, 4.0, 16.0]


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
        self.view_start_ms = 0
        self.selected = None
        self.selected_word = None
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
        self.dirty = True
        self.notify("edit")

    def undo(self) -> bool:
        if self.undo_stack.undo(self) is not None:
            self.dirty = True
            self.notify("edit")
            return True
        return False

    def redo(self) -> bool:
        if self.undo_stack.redo(self) is not None:
            self.dirty = True
            self.notify("edit")
            return True
        return False

    # ------------------------------------------------------------------
    # 導航 / 選取
    # ------------------------------------------------------------------

    @property
    def lines(self) -> list[dict]:
        return self.doc.get("lines") or []

    def clamp_line_i(self, i: int) -> int:
        return max(0, min(i, len(self.lines) - 1))

    def set_current_line(self, i: int) -> None:
        self.current_line_i = self.clamp_line_i(i)
        self.notify("select")

    def set_selection(self, sel: tuple[int, int, int] | None) -> None:
        self.selected = sel
        if sel is not None:
            self.current_line_i = self.clamp_line_i(sel[0])
            self.selected_word = (sel[0], sel[1])
        self.notify("select")

    def set_cursor(self, ms: int) -> None:
        self.cursor_ms = max(0, ms)
        self.notify("cursor")

    @property
    def px_per_ms(self) -> float:
        return ZOOM_LEVELS[self.zoom_idx] / 1000.0

    def zoom(self, direction: int, anchor_ms: int | None = None) -> None:
        """以游標為中心縮放（§6.2）。"""
        anchor = anchor_ms if anchor_ms is not None else self.cursor_ms
        new_idx = max(0, min(len(ZOOM_LEVELS) - 1, self.zoom_idx + direction))
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
