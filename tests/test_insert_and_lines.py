# -*- coding: utf-8 -*-
"""test_insert_and_lines — 加字（#2）＋ 新增／刪除歌詞行（#3）。

用戶 2026-11-11 兩個回報：
2. 逐字微調「想在最後加一個字（如在「記」字後），時間可以是 0.1 秒」——
   舊版 `insert_word()` 只插喺選中字**前面**，而且時長 1ms（等於睇唔到）
3. 歌詞樹「不能在無字幕區段加入一整句 AI 認漏了的歌詞，或任意刪除一句」——
   `AddLine` 永遠寫喺尾行 +3 秒；`_delete_line` 冇選取就靜靜失敗

跑法：pytest，或直跑 ``.venv-ui/Scripts/python.exe tests/test_insert_and_lines.py``
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui import commands, models  # noqa: E402
from ui.state import EditorState  # noqa: E402

PASS: list[str] = []


def check(name: str, cond: bool) -> None:
    PASS.append(name)
    print(("✅ " if cond else "❌ ") + name)
    assert cond, name


def _word(text: str, begin: int, spans: list[tuple[str, int, int]]) -> dict:
    return {
        "text": text, "begin_ms": begin, "end_ms": spans[-1][2],
        "estimated": False, "punctuation": "",
        "chars": [{"text": c, "begin_ms": b, "end_ms": e} for c, b, e in spans],
    }


def _doc(*lines: list[dict]) -> dict:
    return {
        "schema_version": 3,
        "source": {"hold_ms": 300},
        "style": {},
        "lines": [{
            "start_ms": w[0]["begin_ms"], "end_ms": w[-1]["end_ms"],
            "text": "".join(x["text"] for x in w), "break_reason": "", "words": w,
        } for w in lines],
    }


def _chars(line: dict) -> list[tuple[str, int, int]]:
    return [(c["text"], c["begin_ms"], c["end_ms"])
            for w in line["words"] for c in w["chars"]]


# ---------------------------------------------------------------------------
# #2 · 行尾加字（0.1 秒）
# ---------------------------------------------------------------------------

def test_insert_char_after_last_char() -> None:
    """「記」後面加一個字：begin = 記.end、長度 = duration_ms。"""
    st = EditorState()
    st.doc = _doc([_word("記", 16940, [("記", 16940, 17540)])])
    st.push(commands.InsertCharAfter(0, 0, 0, "嗎", duration_ms=100))
    line = st.doc["lines"][0]
    check("#2: 多咗一個詞", len(line["words"]) == 2)
    new_word = line["words"][1]
    check("#2: 新字接住「記」", int(new_word["begin_ms"]) == 17540)
    check("#2: 新字長 100ms", int(new_word["end_ms"]) - int(new_word["begin_ms"]) == 100)
    check("#2: 新字 = estimated（要人手校）", bool(new_word["estimated"]))
    check("#2: 行文字 = 記嗎", line["text"] == "記嗎")
    check("#2: 行 end_ms 跟住延長", int(line["end_ms"]) == 17640)


def test_insert_char_after_mid_word_splits_word() -> None:
    """喺中間字之後加字：個詞要切開，新字夾喺兩段中間（時間無損）。"""
    st = EditorState()
    st.doc = _doc([_word("輪迴塵", 1000,
                         [("輪", 1000, 2000), ("迴", 2000, 3000), ("塵", 3000, 4000)])])
    before = _chars(st.doc["lines"][0])
    st.push(commands.InsertCharAfter(0, 0, 1, "轉", duration_ms=100))
    words = st.doc["lines"][0]["words"]
    check("#2: 詞由 1 個變 3 個", len(words) == 3)
    check("#2: 文字 = 輪迴轉塵", "".join(w["text"] for w in words) == "輪迴轉塵")
    check("#2: 舊 char 時間完全無損", _chars(st.doc["lines"][0])[:2] + [("塵", 3000, 4000)]
          == before)


def test_insert_char_after_clamps_to_next_char() -> None:
    """唔夠位時唔可以蓋過下一個字（唔開連動就夾到剛好）。"""
    st = EditorState()
    st.doc = _doc([_word("你", 1000, [("你", 1000, 2000)]),
                   _word("好", 2000, [("好", 2000, 3000)])])
    st.push(commands.InsertCharAfter(0, 0, 0, "的", duration_ms=500))
    new_word = st.doc["lines"][0]["words"][1]
    check("#2: 夾到唔撞下一個字（最多 1ms 重疊）",
          int(new_word["end_ms"]) <= 2001)


def test_insert_char_after_ripple_shifts_tail() -> None:
    """開「連動」：後面整段順延，字距不變。"""
    st = EditorState()
    st.doc = _doc([_word("你", 1000, [("你", 1000, 2000)]),
                   _word("好", 2000, [("好", 2000, 3000)])])
    st.push(commands.InsertCharAfter(0, 0, 0, "的", duration_ms=500, ripple=True))
    words = st.doc["lines"][0]["words"]
    check("#2: 新字足 500ms", int(words[1]["end_ms"]) - int(words[1]["begin_ms"]) == 500)
    check("#2: 後面的字被推開", int(words[2]["begin_ms"]) == 2500)


def test_insert_char_after_undo() -> None:
    st = EditorState()
    st.doc = _doc([_word("記", 16940, [("記", 16940, 17540)])])
    before = copy.deepcopy(st.doc["lines"][0])
    st.push(commands.InsertCharAfter(0, 0, 0, "嗎", duration_ms=100))
    st.undo()
    check("#2: undo 完全還原", st.doc["lines"][0] == before)


def test_append_moves_line_end_punctuation() -> None:
    """行尾加字：原本句末標點要跟住新字，唔可以變「記 住」。

    2026-11-11 用戶回報：句尾加字後歌詞樹顯示「記　住」（多一個空格）——
    因為 記 帶句末標點，加字後佢唔再係最後一個詞，display_text 就當「行中
    標點」補空格。正解係把標點搬去新嘅最後一個字。
    """
    st = EditorState()
    st.doc = _doc([_word("記", 16940, [("記", 16940, 17540)])])
    st.doc["lines"][0]["words"][0]["punctuation"] = "。"
    st.push(commands.InsertCharAfter(0, 0, 0, "住", duration_ms=100))
    line = st.doc["lines"][0]
    check("#1: 唔會出空格", line["text"] == "記住")
    check("#1: 標點搬去新字", line["words"][1].get("punctuation") == "。")
    check("#1: 原字標點清空", not line["words"][0].get("punctuation"))


def test_insert_mid_line_keeps_space_rule() -> None:
    """行中插字（唔係行尾）唔應該搬標點。"""
    st = EditorState()
    st.doc = _doc([_word("記。", 1000, [("記", 1000, 1500)]),
                   _word("住", 2000, [("住", 2000, 2500)])])
    st.doc["lines"][0]["words"][0]["punctuation"] = "。"
    st.push(commands.InsertCharAfter(0, 0, 0, "緊", duration_ms=100))
    check("#1: 行中標點保持原位",
          st.doc["lines"][0]["words"][0].get("punctuation") == "。")
    check("#1: 行中標點照規則變空格", " " in st.doc["lines"][0]["text"])


def test_insert_char_before_splits_word() -> None:
    """選「去」（屬於「逝去」一個詞）按「＋字前」→ 要插喺逝同去之間。

    2026-11-11 用戶回報：舊版只插喺**整個詞**之前，變成「X逝去」。
    """
    st = EditorState()
    st.doc = _doc([_word("逝去", 19060, [("逝", 19060, 19420), ("去", 19420, 19780)])])
    st.push(commands.InsertCharBefore(0, 0, 1, "X", duration_ms=100))
    line = st.doc["lines"][0]
    check("#1: 位置正確（逝X去）", line["text"] == "逝X去")
    check("#1: 詞序 = 逝/X/去", [w["text"] for w in line["words"]] == ["逝", "X", "去"])
    check("#1: 新字貼住「去」之前", int(line["words"][1]["end_ms"]) == 19420)


def test_insert_char_before_at_word_start() -> None:
    """選詞嘅第一個字 → 插喺詞之前（同舊行為一致）。"""
    st = EditorState()
    st.doc = _doc([_word("逝去", 1000, [("逝", 1000, 1500), ("去", 1500, 2000)])])
    st.push(commands.InsertCharBefore(0, 0, 0, "X", duration_ms=100))
    check("#1: 插喺詞首", st.doc["lines"][0]["text"] == "X逝去")


def test_insert_char_before_undo() -> None:
    st = EditorState()
    st.doc = _doc([_word("逝去", 19060, [("逝", 19060, 19420), ("去", 19420, 19780)])])
    before = copy.deepcopy(st.doc["lines"][0])
    st.push(commands.InsertCharBefore(0, 0, 1, "X", duration_ms=100))
    st.undo()
    check("#1: 加字（前）undo 完全還原", st.doc["lines"][0] == before)


# ---------------------------------------------------------------------------
# #2 · 「✏ 修改」改一個字（唔係改整句）
# ---------------------------------------------------------------------------

def test_set_char_text_single() -> None:
    """改一個字：時間軸完全唔郁。"""
    st = EditorState()
    st.doc = _doc([_word("逝去", 1000, [("逝", 1000, 1500), ("去", 1500, 2000)])])
    st.push(commands.SetCharText(0, 0, 1, "別"))
    line = st.doc["lines"][0]
    check("#2: 只改選中嗰個字", line["text"] == "逝別")
    check("#2: 時間唔郁", _chars(line) == [("逝", 1000, 1500), ("別", 1500, 2000)])
    check("#2: 詞文字同步", line["words"][0]["text"] == "逝別")


def test_set_char_text_rejects_multi() -> None:
    """用戶 2026-11-11 明確要求：改字**只可以輸入一個字**，多過一個字要拒絕。"""
    st = EditorState()
    st.doc = _doc([_word("逝去", 1000, [("逝", 1000, 1500), ("去", 1500, 2000)])])
    st.push(commands.SetCharText(0, 0, 1, "離開"))
    check("#2: 多過一個字唔會改到", st.doc["lines"][0]["text"] == "逝去")
    check("#2: chars 冇變", _chars(st.doc["lines"][0]) == [("逝", 1000, 1500), ("去", 1500, 2000)])


def test_set_char_text_full_width_space() -> None:
    """全形空格要**接受**（用戶要用佢做可見間隔）。"""
    st = EditorState()
    st.doc = _doc([_word("逝去", 1000, [("逝", 1000, 1500), ("去", 1500, 2000)])])
    st.push(commands.SetCharText(0, 0, 1, "　"))
    char = st.doc["lines"][0]["words"][0]["chars"][1]
    check("#3: 全形空格改得入去", char["text"] == "　")
    check("#3: 時間唔郁", (char["begin_ms"], char["end_ms"]) == (1500, 2000))


def test_insert_full_width_space() -> None:
    """加字（前／後）都要接受全形空格。"""
    st = EditorState()
    st.doc = _doc([_word("逝", 1000, [("逝", 1000, 1500)])])
    st.push(commands.InsertCharAfter(0, 0, 0, "　", duration_ms=100))
    new_word = st.doc["lines"][0]["words"][1]
    check("#3: 全形空格加得入去", new_word["text"] == "　")
    check("#3: 有時間（100ms）", int(new_word["end_ms"]) - int(new_word["begin_ms"]) == 100)


def test_set_char_text_empty_refused() -> None:
    st = EditorState()
    st.doc = _doc([_word("逝", 1000, [("逝", 1000, 1500)])])
    st.push(commands.SetCharText(0, 0, 0, "   "))
    check("#2: 空白輸入唔會清空個字", st.doc["lines"][0]["text"] == "逝")


# ---------------------------------------------------------------------------
# #3（新）· 逐字微調「✕ 刪字」
# ---------------------------------------------------------------------------

def test_delete_char_mid_word() -> None:
    """三字詞刪中間一個字 → 詞剩兩字、時間邊界更新。"""
    st = EditorState()
    st.doc = _doc([_word("輪迴塵", 1000,
                         [("輪", 1000, 2000), ("迴", 2000, 3000), ("塵", 3000, 4000)])])
    st.push(commands.DeleteChar(0, 0, 1))
    line = st.doc["lines"][0]
    check("#3: 文字 = 輪塵", line["text"] == "輪塵")
    check("#3: 詞文字同步", line["words"][0]["text"] == "輪塵")
    check("#3: 詞 end 更新", int(line["words"][0]["end_ms"]) == 4000)
    check("#3: 行字數 2", sum(len(w["chars"]) for w in line["words"]) == 2)


def test_delete_only_char_of_word_removes_word() -> None:
    st = EditorState()
    st.doc = _doc([_word("甲", 0, [("甲", 0, 500)]),
                   _word("乙", 600, [("乙", 600, 1000)])])
    st.push(commands.DeleteChar(0, 0, 0))
    line = st.doc["lines"][0]
    check("#3: 空詞被移除", len(line["words"]) == 1)
    check("#3: 只剩乙", line["text"] == "乙")


def test_delete_char_refuses_last_char() -> None:
    """行只剩一個字：拒絕（避免零字行），提示改用刪行。"""
    st = EditorState()
    st.doc = _doc([_word("記", 0, [("記", 0, 500)])])
    st.push(commands.DeleteChar(0, 0, 0))
    check("#3: 唔會刪到零字", st.doc["lines"][0]["text"] == "記")


def test_delete_char_undo() -> None:
    st = EditorState()
    st.doc = _doc([_word("輪迴塵", 1000,
                         [("輪", 1000, 2000), ("迴", 2000, 3000), ("塵", 3000, 4000)])])
    before = copy.deepcopy(st.doc["lines"][0])
    st.push(commands.DeleteChar(0, 0, 1))
    st.undo()
    check("#3: 刪字 undo 完全還原", st.doc["lines"][0] == before)


# ---------------------------------------------------------------------------
# #3 · 無字幕區段新增整句 / 任意刪行
# ---------------------------------------------------------------------------

def test_add_line_into_gap() -> None:
    """喺第 1、2 行之間（無字幕區段）新增一句：按時間插入，唔會去到尾。"""
    st = EditorState()
    st.doc = _doc([_word("第一句", 0, [("第", 0, 500)])],
                  [_word("第二句", 5000, [("第", 5000, 5500)])])
    st.push(commands.AddLine(2000, 3500, "AI 漏咗嘅一句"))
    lines = st.doc["lines"]
    check("#3: 行數 +1", len(lines) == 3)
    check("#3: 插喺中間（第 2 行）", lines[1]["text"] == "AI 漏咗嘅一句")
    check("#3: 時間 = 2000→3500",
          (int(lines[1]["start_ms"]), int(lines[1]["end_ms"])) == (2000, 3500))
    check("#3: 每字都有時間",
          all(int(c["end_ms"]) > int(c["begin_ms"]) for c in lines[1]["words"][0]["chars"]))


def test_add_line_at_index() -> None:
    """右鍵「喺此行之前插入」：at_line_i 指定位置（即使時間唔順序）。"""
    st = EditorState()
    st.doc = _doc([_word("甲", 0, [("甲", 0, 500)])],
                  [_word("乙", 5000, [("乙", 5000, 5500)])])
    st.push(commands.AddLine(0, 100, "丙", at_line_i=1))
    check("#3: 插喺第 2 個位置", st.doc["lines"][1]["text"] == "丙")
    check("#3: 原本第 2 行變第 3 行", st.doc["lines"][2]["text"] == "乙")


def test_add_line_undo_uses_identity() -> None:
    """兩行文字／時間一樣時，undo 要刪返啱嗰行（唔可以 by-value 搵索引）。"""
    st = EditorState()
    st.doc = _doc([_word("同", 0, [("同", 0, 500)])])
    st.push(commands.AddLine(0, 500, "同"))
    check("#3: 有兩行相同內容", len(st.doc["lines"]) == 2)
    st.undo()
    check("#3: undo 後只剩一行", len(st.doc["lines"]) == 1)


def test_delete_any_line_and_undo() -> None:
    st = EditorState()
    st.doc = _doc([_word("一", 0, [("一", 0, 500)])],
                  [_word("二", 1000, [("二", 1000, 1500)])],
                  [_word("三", 2000, [("三", 2000, 2500)])])
    before = copy.deepcopy(st.doc["lines"])
    st.push(commands.DeleteLine(1))
    check("#3: 中間行刪得走", [l["text"] for l in st.doc["lines"]] == ["一", "三"])
    st.undo()
    check("#3: undo 還原", st.doc["lines"] == before)


def test_delete_last_line_and_undo() -> None:
    st = EditorState()
    st.doc = _doc([_word("一", 0, [("一", 0, 500)])],
                  [_word("二", 1000, [("二", 1000, 1500)])])
    st.push(commands.DeleteLine(1))
    check("#3: 尾行刪得走", len(st.doc["lines"]) == 1)
    st.undo()
    check("#3: 尾行 undo 還原", len(st.doc["lines"]) == 2)


# ---------------------------------------------------------------------------
# 直跑入口
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        test_insert_char_after_last_char,
        test_insert_char_after_mid_word_splits_word,
        test_insert_char_after_clamps_to_next_char,
        test_insert_char_after_ripple_shifts_tail,
        test_insert_char_after_undo,
        test_append_moves_line_end_punctuation,
        test_insert_mid_line_keeps_space_rule,
        test_insert_char_before_splits_word,
        test_insert_char_before_at_word_start,
        test_insert_char_before_undo,
        test_set_char_text_single,
        test_set_char_text_rejects_multi,
        test_set_char_text_full_width_space,
        test_insert_full_width_space,
        test_set_char_text_empty_refused,
        test_delete_char_mid_word,
        test_delete_only_char_of_word_removes_word,
        test_delete_char_refuses_last_char,
        test_delete_char_undo,
        test_add_line_into_gap,
        test_add_line_at_index,
        test_add_line_undo_uses_identity,
        test_delete_any_line_and_undo,
        test_delete_last_line_and_undo,
    ]
    failed: list[tuple[str, str]] = []
    for case in cases:
        try:
            case()
        except AssertionError as exc:
            failed.append((case.__name__, str(exc)))
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(cases)}")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1
    print(f"ALL PASS ({len(PASS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
