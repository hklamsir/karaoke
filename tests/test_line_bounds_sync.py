# -*- coding: utf-8 -*-
"""ADR-040 行邊界跟隨逐字改動的契約測試。

背景：逐字微調平移了字之後，歌詞樹（顯示行 ``start_ms`` / ``end_ms``）仍然
停在舊時間。根因是 ``sync_word_bounds()`` 只重算 word 邊界，冇連動行邊界。

鎖住四件事：
1. 改首字 → 行 ``start_ms`` 跟隨；改尾字 → 行 ``end_ms`` 跟隨
2. 只改中間字 → 行邊界原封不動
3. 句尾 hold（``end_ms`` 與尾字 end 的差）唔會被吞掉
4. undo 之後行邊界完全還原
"""
from __future__ import annotations

import copy

import pytest

from ui import commands, models


def _doc(*, start: int = 1000, end: int = 4500, tail: int = 4000) -> dict:
    """一行三個字：1000-2000 / 2000-3000 / 3000-4000，行 1000-4500（hold 500）。"""
    return {"lines": [{
        "start_ms": start, "end_ms": end, "text": "abc",
        "words": [
            {"text": "a", "begin_ms": 1000, "end_ms": 2000,
             "chars": [{"text": "a", "begin_ms": 1000, "end_ms": 2000}]},
            {"text": "b", "begin_ms": 2000, "end_ms": 3000,
             "chars": [{"text": "b", "begin_ms": 2000, "end_ms": 3000}]},
            {"text": "c", "begin_ms": 3000, "end_ms": tail,
             "chars": [{"text": "c", "begin_ms": 3000, "end_ms": tail}]},
        ],
    }]}


class _State:
    """最小 state（命令只需要 ``doc``）。"""

    def __init__(self, doc: dict) -> None:
        self.doc = doc


def _nudge(doc: dict, targets: list[tuple[int, int]], delta: int) -> dict:
    state = _State(doc)
    commands.NudgeChars(0, targets, delta).apply(state)
    return doc["lines"][0]


# ---------------------------------------------------------------------------
# 行邊界跟隨
# ---------------------------------------------------------------------------

def test_nudge_tail_updates_line_end() -> None:
    """平移尾字 -100 → 行 end 由 4500 變 4400。"""
    line = _nudge(_doc(), [(2, 0)], -100)
    assert line["end_ms"] == 4400
    assert line["start_ms"] == 1000, "尾字唔應該動到行首"


def test_nudge_head_updates_line_start() -> None:
    """平移首字 +150 → 行 start 由 1000 變 1150。"""
    line = _nudge(_doc(), [(0, 0)], 150)
    assert line["start_ms"] == 1150
    assert line["end_ms"] == 4500, "首字唔應該動到行尾"


def test_nudge_middle_keeps_line_bounds() -> None:
    """只改中間字 → 行邊界完全不變（首尾字沒動）。"""
    line = _nudge(_doc(), [(1, 0)], -100)
    assert (line["start_ms"], line["end_ms"]) == (1000, 4500)


def test_nudge_head_cannot_go_negative() -> None:
    """首字推到 0 之前會 clamp；行 start 亦跟住夾到 0，唔會變負。"""
    line = _nudge(_doc(start=100), [(0, 0)], -1000)
    assert line["words"][0]["chars"][0]["begin_ms"] == 0
    assert line["start_ms"] == 0


def test_tail_hold_is_preserved() -> None:
    """行 end − 尾字 end 的 hold（500ms）喺平移後要保持，唔可以被吞。"""
    line = _nudge(_doc(), [(2, 0)], -100)
    hold = line["end_ms"] - line["words"][-1]["end_ms"]
    assert hold == 500


def test_both_edges_nudge_together() -> None:
    """首尾同時選中 → 兩邊一齊跟隨。"""
    line = _nudge(_doc(), [(0, 0), (2, 0)], -100)
    assert (line["start_ms"], line["end_ms"]) == (900, 4400)


# ---------------------------------------------------------------------------
# undo / 其他命令
# ---------------------------------------------------------------------------

def test_undo_restores_line_bounds() -> None:
    """revert 之後行邊界同逐字時間都要返原狀（唔可以只還原字）。"""
    doc = _doc()
    original = copy.deepcopy(doc)
    state = _State(doc)
    cmd = commands.NudgeChars(0, [(0, 0), (2, 0)], -100)
    cmd.apply(state)
    assert (doc["lines"][0]["start_ms"], doc["lines"][0]["end_ms"]) == (900, 4400)

    cmd.revert(state)
    assert doc == original


def test_ripple_updates_line_end() -> None:
    """連動順延改尾字結束時間，行 end 要跟住行。"""
    doc = _doc()
    commands.SetCharTimingRipple(0, 2, (3000, 4200)).apply(_State(doc))
    assert doc["lines"][0]["end_ms"] == 4700        # 4500 + 200


def test_redistribute_whole_span_keeps_bounds_when_ends_fixed() -> None:
    """keep_ends=False 但首尾字時間本質不變（已對齊）→ 行邊界不動。"""
    doc = _doc()
    commands.RedistributeSpan(0, 0, 2, keep_ends=False).apply(_State(doc))
    line = doc["lines"][0]
    assert (line["start_ms"], line["end_ms"]) == (1000, 4500)


@pytest.mark.parametrize("targets,delta", [([(2, 0)], 200), ([(0, 0)], -300)])
def test_other_lines_untouched(targets: list[tuple[int, int]], delta: int) -> None:
    """改一行唔可以影響另一行（差量同步必須以本行為界）。"""
    doc = _doc()
    doc["lines"].append({
        "start_ms": 6000, "end_ms": 9000, "text": "de",
        "words": [{"text": "de", "begin_ms": 6000, "end_ms": 8500,
                   "chars": [{"text": "d", "begin_ms": 6000, "end_ms": 7000},
                             {"text": "e", "begin_ms": 7000, "end_ms": 8500}]}],
    })
    _nudge(doc, targets, delta)
    assert (doc["lines"][1]["start_ms"], doc["lines"][1]["end_ms"]) == (6000, 9000)


def test_sync_threads_word_bounds_as_before() -> None:
    """回歸保護：word 邊界仍然由首尾 char 推導（ADR-040 唔可以破壞原本職責）。"""
    line = _doc()["lines"][0]
    line["words"][1]["begin_ms"] = 999       # 人為 drift
    models.sync_word_bounds(line)
    assert line["words"][1]["begin_ms"] == 2000
    assert line["words"][1]["end_ms"] == 3000


# ---------------------------------------------------------------------------
# 「✓ 確定」走 SetCharTiming —— 用戶回報改完首字時間，歌詞樹唔更新
# ---------------------------------------------------------------------------

def test_set_char_timing_head_updates_line_start() -> None:
    """逐字面板「確定」改首字開始 → 行 start 要跟（唔可以只改 word）。"""
    doc = _doc()
    commands.SetCharTiming(0, 0, 0, old=(1000, 2000), new=(1200, 2000)).apply(_State(doc))
    assert doc["lines"][0]["start_ms"] == 1200
    assert doc["lines"][0]["end_ms"] == 4500


def test_set_char_timing_tail_updates_line_end() -> None:
    """改尾字結束 → 行 end 跟，句尾 hold 保留。"""
    doc = _doc()
    commands.SetCharTiming(0, 2, 0, old=(3000, 4000), new=(3000, 4200)).apply(_State(doc))
    line = doc["lines"][0]
    assert line["end_ms"] == 4700
    assert line["end_ms"] - line["words"][-1]["end_ms"] == 500


def test_set_char_timing_middle_keeps_line_bounds() -> None:
    """改中間字 → 行邊界不動。"""
    doc = _doc()
    commands.SetCharTiming(0, 1, 0, old=(2000, 3000), new=(2100, 3000)).apply(_State(doc))
    assert (doc["lines"][0]["start_ms"], doc["lines"][0]["end_ms"]) == (1000, 4500)


def test_set_char_timing_undo_restores_everything() -> None:
    """revert 要連行邊界一齊還原（連 estimated 旗標）。"""
    doc = _doc()
    doc["lines"][0]["words"][0]["estimated"] = True
    original = copy.deepcopy(doc)
    state = _State(doc)
    cmd = commands.SetCharTiming(0, 0, 0, old=(1000, 2000), new=(1200, 2000))
    cmd.apply(state)
    cmd.revert(state)
    assert doc == original


def test_set_char_timing_reapply_is_not_cumulative() -> None:
    """連續撳箭咀（原地 re-apply）唔會令行邊界累加偏移。"""
    doc = _doc()
    state = _State(doc)
    cmd = commands.SetCharTiming(0, 0, 0, old=(1000, 2000), new=(1100, 2000))
    cmd.apply(state)
    cmd.new = (1300, 2000)               # 呼叫方更新目標值後再 apply
    cmd.apply(state)
    assert doc["lines"][0]["start_ms"] == 1300
