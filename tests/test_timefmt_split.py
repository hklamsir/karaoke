# -*- coding: utf-8 -*-
"""test_timefmt_split — 時間碼 + 跨字均分／修補（用戶 2026-11-03 回報）。

背景（真實案例，第 39 句）：
    AI 漏咗「那裡保證何時能突破」整段詞 → 8 個字擠喺 3:41.995–3:42.958
    （每個 ~107ms），用戶手動把「破」的結束時間改成 3:53.158，但中間 7 個字
    原封不動，程式「不懂自動分配每个字的平均估計時間」。同時逐字微調面板只用
    裸毫秒，用戶唔知個字喺曲中第幾分幾秒。

呢個檔案守住三件事：
1. ``fmt_ms`` / ``parse_time`` 對稱（顯示咩就打得返咩，舊純 ms 仍然接受）
2. ``redistribute_span(keep_ends=True)`` 保留首末字、只攤中間
3. ``repair_line()`` 對「一堆短字 + 一個超長字」判為整行崩壞並全段重攤
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui import commands, models, timefmt  # noqa: E402
from ui.state import EditorState  # noqa: E402


# ---------------------------------------------------------------------------
# 測試資料
# ---------------------------------------------------------------------------

def _broken_line() -> dict:
    """第 39 句的真實形狀：8 個字擠成一秒，尾字被拉長到 3:53.158。"""
    text = "那裡保證何時能突破"
    spans = models.proportional_split(221_995, 222_958, len(text))
    chars = [{"text": ch, "begin_ms": b, "end_ms": e}
             for ch, (b, e) in zip(text, spans)]
    chars[-1]["end_ms"] = 233_158
    return {
        "start_ms": 221_995, "end_ms": 233_158, "text": text,
        "break_reason": "sentence",
        "words": [{"text": text, "begin_ms": 221_995, "end_ms": 233_158,
                   "estimated": True, "punctuation": "", "chars": chars}],
    }


def _healthy_line() -> dict:
    """正常一行：5 個字，每個 ~500ms（唔應該被修補碰到）。"""
    text = "何時仍要歌唱"
    spans = models.proportional_split(238_270, 240_770, len(text))
    return {
        "start_ms": 238_270, "end_ms": 240_770, "text": text, "break_reason": "",
        "words": [{"text": text, "begin_ms": 238_270, "end_ms": 240_770,
                   "estimated": False, "punctuation": "",
                   "chars": [{"text": ch, "begin_ms": b, "end_ms": e}
                             for ch, (b, e) in zip(text, spans)]}],
    }


def _two_word_line() -> dict:
    """跨兩個 word 的一行：用來驗證「均分」真係跨詞（唔止 word 內部）。"""
    return {
        "start_ms": 10_000, "end_ms": 12_000, "text": "甲乙丙丁戊", "break_reason": "",
        "words": [
            {"text": "甲乙", "begin_ms": 10_000, "end_ms": 10_400, "estimated": False,
             "punctuation": "",
             "chars": [{"text": "甲", "begin_ms": 10_000, "end_ms": 10_200},
                       {"text": "乙", "begin_ms": 10_200, "end_ms": 10_400}]},
            {"text": "丙丁戊", "begin_ms": 11_000, "end_ms": 12_000, "estimated": False,
             "punctuation": "",
             "chars": [{"text": "丙", "begin_ms": 11_000, "end_ms": 11_300},
                       {"text": "丁", "begin_ms": 11_300, "end_ms": 11_600},
                       {"text": "戊", "begin_ms": 11_600, "end_ms": 12_000}]},
        ],
    }


# ---------------------------------------------------------------------------
# timefmt
# ---------------------------------------------------------------------------

def test_fmt_ms_basic() -> None:
    assert timefmt.fmt_ms(221_995) == "3:41.995"
    assert timefmt.fmt_ms(0) == "0:00.000"
    assert timefmt.fmt_ms(59_999) == "0:59.999"
    assert timefmt.fmt_ms(3_821_995) == "1:03:41.995"
    assert timefmt.fmt_ms(-50) == "0:00.000"          # 負數夾返 0，唔好出 "-1:59.999"


@pytest.mark.parametrize("text, expected", [
    ("3:41.995", 221_995),
    ("3:41", 221_000),
    ("1:03:41.995", 3_821_995),
    ("221.995", 221_995),          # 有小數點無冒號 = 秒
    ("221995", 221_995),           # 純整數 = 毫秒（舊 UI 肌肉記憶）
    ("3：41.995", 221_995),        # 全形冒號
    (" 3:41.995 ", 221_995),
])
def test_parse_time_accepts(text: str, expected: int) -> None:
    assert timefmt.parse_time(text) == expected


def test_parse_time_roundtrip() -> None:
    """顯示咩就一定要打得返咩（否則用戶 copy 自己嘅值都會出錯）。"""
    for ms in (0, 1_000, 59_999, 221_995, 233_158, 3_821_995):
        assert timefmt.parse_time(timefmt.fmt_ms(ms)) == ms


@pytest.mark.parametrize("bad", ["", "   ", "-1", "abc", "3:61", "3:41:61", "1:2:3:4"])
def test_parse_time_rejects(bad: str) -> None:
    with pytest.raises(timefmt.TimeParseError):
        timefmt.parse_time(bad)


def test_fmt_span_shows_duration() -> None:
    assert timefmt.fmt_span(221_995, 233_158) == "3:41.995 → 3:53.158（11.163s）"


# ---------------------------------------------------------------------------
# 跨字均分
# ---------------------------------------------------------------------------

def test_redistribute_keep_ends_preserves_anchors() -> None:
    """⊞ 均分：首字、末字一個 ms 都唔准郁，中間平均攤。"""
    line = _two_word_line()
    before = [copy.deepcopy(c) for _w, _c, c in models.iter_flat_chars(line)]

    plan = models.redistribute_span(line, 0, 4, keep_ends=True)

    assert plan is not None
    chars = [c for _w, _c, c in models.iter_flat_chars(line)]
    assert (chars[0]["begin_ms"], chars[0]["end_ms"]) == (before[0]["begin_ms"], before[0]["end_ms"])
    assert (chars[-1]["begin_ms"], chars[-1]["end_ms"]) == (before[-1]["begin_ms"], before[-1]["end_ms"])
    # 中間 3 個字要首尾相接、無空隙、無重疊
    for a, b in zip(chars[1:4], chars[2:5]):
        assert a["end_ms"] == b["begin_ms"]
    assert chars[1]["begin_ms"] == before[0]["end_ms"]        # 由首字尾巴開始
    assert chars[3]["end_ms"] == before[-1]["begin_ms"]       # 攤到末字開頭為止


def test_redistribute_marks_estimated_and_syncs_word_bounds() -> None:
    line = _two_word_line()
    models.redistribute_span(line, 0, 4, keep_ends=True)
    for word in line["words"]:
        assert word["estimated"] is True                     # 均分結果仍然係估算
        assert word["begin_ms"] == word["chars"][0]["begin_ms"]
        assert word["end_ms"] == word["chars"][-1]["end_ms"]


def test_redistribute_needs_room_and_three_chars() -> None:
    line = _two_word_line()
    # 首字 end (10.4s) 已經過咗末字 begin (11.0s)? 唔係 —— 但只揀 2 個字冇中間位
    assert models.span_plan(line, 0, 1, keep_ends=True) is None
    assert models.redistribute_span(line, 0, 1, keep_ends=True) is None
    # 全段模式 2 個字 OK
    assert models.span_plan(line, 0, 1, keep_ends=False)["count"] == 2


def test_redistribute_keep_ends_no_room() -> None:
    line = _two_word_line()
    # 揀 甲(index0) 到 乙(index1) 再拉埋丙 → 中間只有乙，但首.end 10.4 < 末.begin 11.0 有位
    assert models.span_plan(line, 0, 2, keep_ends=True) is not None
    # 反過來：首字（甲）end 已經越過末字（丙）begin → 冇位可攤
    line["words"][0]["chars"][0]["end_ms"] = 11_500
    assert models.span_plan(line, 0, 2, keep_ends=True) is None


# ---------------------------------------------------------------------------
# 修補（漏字崩壞）
# ---------------------------------------------------------------------------

def test_repair_line_fixes_user_sentence_39() -> None:
    """真實案例：9 個字要平均攤滿 3:41.995 → 3:53.158。"""
    line = _broken_line()
    plan = models.repair_plan(line)
    assert plan["mode"] == "line"
    assert plan["count"] == 9

    report = models.repair_line(line)
    assert report["mode"] == "line"
    assert report["count"] == 9

    chars = [c for _w, _c, c in models.iter_flat_chars(line)]
    assert chars[0]["begin_ms"] == 221_995           # 頭
    assert chars[-1]["end_ms"] == 233_158            # 尾（用戶手動量度的錨）
    durs = [c["end_ms"] - c["begin_ms"] for c in chars]
    assert max(durs) - min(durs) <= 2                # 每個字 ~1.24s，差 ≤2ms（四捨五入）
    assert all(c["end_ms"] > c["begin_ms"] for c in chars)
    for a, b in zip(chars, chars[1:]):
        assert a["end_ms"] == b["begin_ms"]
    assert models.doc_stats({"lines": [line]})["zero_chars"] == 0


def test_repair_line_leaves_healthy_line_alone() -> None:
    line = _healthy_line()
    before = copy.deepcopy(line)
    assert models.repair_plan(line)["mode"] == "none"
    report = models.repair_line(line)
    assert report["mode"] == "none"
    assert line == before          # 一個 ms 都唔准改


def test_repair_line_fills_local_zero_run_only() -> None:
    """局部崩壞：只補中間兩個 0ms 字，前後正常字唔准郁。"""
    line = _healthy_line()
    chars = [c for _w, _c, c in models.iter_flat_chars(line)]
    chars[2]["end_ms"] = chars[2]["begin_ms"]      # 兩個字變零時長
    chars[3]["end_ms"] = chars[3]["begin_ms"]
    keep_before = [(c["begin_ms"], c["end_ms"]) for c in chars[:2]]

    plan = models.repair_plan(line)
    assert plan["mode"] == "spans"
    report = models.repair_line(line)
    assert report["mode"] == "spans"
    assert report["count"] == 2

    chars = [c for _w, _c, c in models.iter_flat_chars(line)]
    assert [(c["begin_ms"], c["end_ms"]) for c in chars[:2]] == keep_before
    assert all(c["end_ms"] > c["begin_ms"] for c in chars)


# ---------------------------------------------------------------------------
# undo / noop
# ---------------------------------------------------------------------------

def _state_with(line: dict) -> EditorState:
    st = EditorState()
    st.doc = {"source": {}, "style": {}, "lines": [copy.deepcopy(line)]}
    st.path = Path("_test_.json")
    return st


def test_redistribute_undo_is_exact() -> None:
    st = _state_with(_two_word_line())
    before = copy.deepcopy(st.doc)
    st.push(commands.RedistributeSpan(0, 0, 4, keep_ends=True))
    assert st.doc != before
    assert st.undo()
    assert st.doc == before


def test_repair_undo_is_exact() -> None:
    st = _state_with(_broken_line())
    before = copy.deepcopy(st.doc)
    st.push(commands.RepairLine(0))
    assert st.doc != before
    assert st.undo()
    assert st.doc == before


def test_nudge_shifts_only_selected_chars() -> None:
    st = _state_with(_two_word_line())
    before = [copy.deepcopy(c) for _w, _c, c in models.iter_flat_chars(st.doc["lines"][0])]
    st.push(commands.NudgeChars(0, [(0, 0), (0, 1)], 50))
    chars = [c for _w, _c, c in models.iter_flat_chars(st.doc["lines"][0])]
    assert chars[0]["begin_ms"] == before[0]["begin_ms"] + 50
    assert chars[1]["end_ms"] == before[1]["end_ms"] + 50
    assert chars[2]["begin_ms"] == before[2]["begin_ms"]      # 冇選中 → 唔郁


def test_noop_does_not_pollute_undo_or_dirty() -> None:
    """均分冇位可攤 = 冇改動 → 唔應該佔 undo 一步、亦唔應該標「未儲存」。"""
    st = _state_with(_two_word_line())
    st.dirty = False
    st.push(commands.RedistributeSpan(0, 0, 1, keep_ends=True))
    assert not st.undo_stack.can_undo()
    assert st.dirty is False
