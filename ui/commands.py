# -*- coding: utf-8 -*-
"""commands — Undo / Redo（E1：Command pattern，深度 100，Ctrl+Z / Ctrl+Shift+Z）。

tkinter 冇內建 undo stack，全部自己寫。結構性操作（斷句/合併/插入字/改文字）
用**行級 snapshot**（deepcopy），精確操作（改時間）用差量 —— 記憶體細、還原零漂移
（V4：隨機 100 步操作全 undo 後 doc 要 byte-identical）。
"""

from __future__ import annotations

import copy
from typing import Any, Protocol

from . import models

MAX_DEPTH = 100


class Command(Protocol):
    def apply(self, state: Any) -> None: ...
    def revert(self, state: Any) -> None: ...


# ---------------------------------------------------------------------------
# 差量 command
# ---------------------------------------------------------------------------

class SetCharTiming:
    """改一個字嘅 [begin, end]（拖曳 / 逐字微調）。N7：順手清除該 word 嘅 estimated。"""

    def __init__(self, line_i: int, word_i: int, char_i: int,
                 old: tuple[int, int], new: tuple[int, int]) -> None:
        self.line_i, self.word_i, self.char_i = line_i, word_i, char_i
        self.old, self.new = old, new
        self._old_estimated: bool | None = None

    def apply(self, state: Any) -> None:
        line = state.doc["lines"][self.line_i]
        word = models.word_of(line, self.word_i)
        char = models.char_of(line, self.word_i, self.char_i)
        if char is None or word is None:
            return
        char["begin_ms"], char["end_ms"] = self.new
        word["begin_ms"], word["end_ms"] = word["chars"][0]["begin_ms"], word["chars"][-1]["end_ms"]
        self._old_estimated = bool(word.get("estimated", False))
        word["estimated"] = False     # N7：人手校過 → 清 flag

    def revert(self, state: Any) -> None:
        line = state.doc["lines"][self.line_i]
        word = models.word_of(line, self.word_i)
        char = models.char_of(line, self.word_i, self.char_i)
        if char is None or word is None:
            return
        char["begin_ms"], char["end_ms"] = self.old
        word["begin_ms"], word["end_ms"] = word["chars"][0]["begin_ms"], word["chars"][-1]["end_ms"]
        if self._old_estimated is not None:
            word["estimated"] = self._old_estimated


class SetWordShift:
    """拖曳字塊本體 = 整個 word 平移（C3：word 內所有 char 跟住郁）。"""

    def __init__(self, line_i: int, word_i: int, delta_ms: int,
                 originals: list[tuple[int, int]]) -> None:
        self.line_i, self.word_i, self.delta_ms = line_i, word_i, delta_ms
        self._originals = list(originals)     # 每 char 嘅 (begin, end)
        self._old_estimated: bool | None = None

    def _apply_delta(self, state: Any, sign: int) -> None:
        line = state.doc["lines"][self.line_i]
        word = models.word_of(line, self.word_i)
        if word is None:
            return
        d = self.delta_ms * sign
        for char, (b, e) in zip(word.get("chars") or [], self._originals):
            char["begin_ms"], char["end_ms"] = b + d, e + d
        word["begin_ms"], word["end_ms"] = word["chars"][0]["begin_ms"], word["chars"][-1]["end_ms"]
        if sign > 0:
            # N7：人手校過 → 清 flag（revert 時還原）
            self._old_estimated = bool(word.get("estimated", False))
            word["estimated"] = False
        elif self._old_estimated is not None:
            word["estimated"] = self._old_estimated

    def apply(self, state: Any) -> None:
        self._apply_delta(state, +1)

    def revert(self, state: Any) -> None:
        line = state.doc["lines"][self.line_i]
        word = models.word_of(line, self.word_i)
        if word is None:
            return
        for char, (b, e) in zip(word.get("chars") or [], self._originals):
            char["begin_ms"], char["end_ms"] = b, e
        word["begin_ms"], word["end_ms"] = word["chars"][0]["begin_ms"], word["chars"][-1]["end_ms"]
        if self._old_estimated is not None:
            word["estimated"] = self._old_estimated


# ---------------------------------------------------------------------------
# Snapshot command（結構性操作）
# ---------------------------------------------------------------------------

class _LineSnapshotMixin:
    def _snapshot(self, state: Any, indices: list[int]) -> None:
        self._indices = indices
        self._before = [copy.deepcopy(state.doc["lines"][i]) for i in indices]

    def _restore(self, state: Any) -> None:
        for i, snap in zip(self._indices, self._before):
            state.doc["lines"][i] = copy.deepcopy(snap)


class SetLineText(_LineSnapshotMixin):
    """改一行文字（C1 表格改字）。"""

    def __init__(self, line_i: int, new_text: str) -> None:
        self.line_i, self.new_text = line_i, new_text

    def apply(self, state: Any) -> None:
        self._snapshot(state, [self.line_i])
        models.set_line_text(state.doc["lines"][self.line_i], self.new_text)

    def revert(self, state: Any) -> None:
        self._restore(state)


class SplitLine(_LineSnapshotMixin):
    """斷句（C5）：喺 word 邊界插入新行。"""

    def __init__(self, line_i: int, word_i: int) -> None:
        self.line_i, self.word_i = line_i, word_i

    def apply(self, state: Any) -> None:
        self._snapshot(state, [self.line_i])
        new_line = models.split_line_at_word(state.doc["lines"][self.line_i], self.word_i)
        if new_line is not None:
            state.doc["lines"].insert(self.line_i + 1, new_line)
            self._inserted = True
        else:
            self._inserted = False

    def revert(self, state: Any) -> None:
        if self._inserted:
            state.doc["lines"].pop(self.line_i + 1)
        self._restore(state)


class MergeLines(_LineSnapshotMixin):
    """合併相鄰行（C5）：count 行併成一行；中間空隙唔填。"""

    def __init__(self, line_i: int, count: int = 2) -> None:
        self.line_i, self.count = line_i, count

    def apply(self, state: Any) -> None:
        indices = list(range(self.line_i, self.line_i + self.count))
        self._snapshot(state, indices)
        lines = state.doc["lines"]
        if self.line_i + self.count > len(lines):
            return
        first = lines[self.line_i]
        for other in lines[self.line_i + 1: self.line_i + self.count]:
            models.merge_lines(first, other)
        self._removed = [copy.deepcopy(l) for l in lines[self.line_i + 1: self.line_i + self.count]]
        del lines[self.line_i + 1: self.line_i + self.count]

    def revert(self, state: Any) -> None:
        for k, snap in enumerate(self._removed):
            state.doc["lines"].insert(self.line_i + 1 + k, copy.deepcopy(snap))
        self._restore(state)


class InsertWord(_LineSnapshotMixin):
    """行中插入一個字（C6 場景 B）：新單字 WordGroup，原 char 時間軸不變。"""

    def __init__(self, line_i: int, word_i: int, text: str) -> None:
        self.line_i, self.word_i, self.text = line_i, word_i, text

    def apply(self, state: Any) -> None:
        self._snapshot(state, [self.line_i])
        models.insert_word(state.doc["lines"][self.line_i], self.word_i, self.text)

    def revert(self, state: Any) -> None:
        self._restore(state)


class AddLine(_LineSnapshotMixin):
    """新增整行（C6 場景 A）：拖時間範圍 → 文字按字數均分 → 全部 estimated。"""

    def __init__(self, start_ms: int, end_ms: int, text: str) -> None:
        self.start_ms, self.end_ms, self.text = start_ms, end_ms, text
        self.inserted_i: int | None = None

    def apply(self, state: Any) -> None:
        line = models.add_line(state.doc, self.start_ms, self.end_ms, self.text)
        self.inserted_i = state.doc["lines"].index(line)

    def revert(self, state: Any) -> None:
        if self.inserted_i is not None:
            state.doc["lines"].pop(self.inserted_i)


class DeleteLine(_LineSnapshotMixin):
    """刪除整行（工具列 / 快捷鍵）。"""

    def __init__(self, line_i: int) -> None:
        self.line_i = line_i

    def apply(self, state: Any) -> None:
        self._snapshot(state, [self.line_i])
        self._snap_line = copy.deepcopy(state.doc["lines"][self.line_i])
        state.doc["lines"].pop(self.line_i)

    def revert(self, state: Any) -> None:
        state.doc["lines"].insert(self.line_i, copy.deepcopy(self._snap_line))


# ---------------------------------------------------------------------------
# UndoStack
# ---------------------------------------------------------------------------

class UndoStack:
    """深度 100（E1）。``can_undo`` / ``can_redo`` 畀工具列掣做狀態。"""

    def __init__(self, depth: int = MAX_DEPTH) -> None:
        self._depth = depth
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    def push(self, cmd: Command, state: Any) -> None:
        cmd.apply(state)
        self._undo.append(cmd)
        if len(self._undo) > self._depth:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self, state: Any) -> Command | None:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.revert(state)
        self._redo.append(cmd)
        return cmd

    def redo(self, state: Any) -> Command | None:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.apply(state)
        self._undo.append(cmd)
        return cmd

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
