# -*- coding: utf-8 -*-
"""test_editor_core — 編輯器純邏輯驗證（V3 / V4 / V6 / V7；無 Tk，直接跑）。

用法：.venv-ui/Scripts/python.exe tests/test_editor_core.py
"""

from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import commands, models  # noqa: E402
from ui.state import EditorState  # noqa: E402
from ui.widgets.timeline import EDGE_PX, compute_layout, hit_test, ms_at_x  # noqa: E402

SMOKE = next(iter(Path("_smoke").glob("*_timings.json")), None)
PASS: list[str] = []


def check(name: str, cond: bool) -> None:
    PASS.append(name)
    print(("✅" if cond else "❌"), name)
    if not cond:
        raise SystemExit(f"FAIL: {name}")


# ---------------------------------------------------------------------------
# V3 · hit-test
# ---------------------------------------------------------------------------

def test_v3_hit_test() -> None:
    lines = [{
        "start_ms": 1000, "end_ms": 2000, "text": "你好嗎", "break_reason": "",
        "words": [{
            "text": "你好嗎", "begin_ms": 1000, "end_ms": 2000, "estimated": False,
            "punctuation": "",
            "chars": [
                {"text": "你", "begin_ms": 1000, "end_ms": 1300},
                {"text": "好", "begin_ms": 1300, "end_ms": 1600},
                {"text": "嗎", "begin_ms": 1600, "end_ms": 2000},
            ],
        }],
    }]
    ppm = 0.1     # 1ms = 0.1px → 300ms = 30px
    rects = compute_layout(lines, ppm, view_start_ms=0, canvas_w=1000, y_top=20)
    check("V3: layout 有 3 個 char rect", len(rects) == 3)

    # 字塊內（第二個字正中）
    mode, ref = hit_test(rects, (1300 + 1600) / 2 * ppm, 25)
    check(f"V3: body → {ref}", mode == "body" and ref == (0, 0, 1))
    # 左邊界 5px 內
    x0, _, x1, _ = rects[1][3], rects[1][4], rects[1][5], rects[1][6]
    mode, ref = hit_test(rects, x0 + 2, 25)
    check(f"V3: left edge → {ref}", mode == "left" and ref == (0, 0, 1))
    # 右邊界（同 char2 左界共享 → 返 char1.right 或 char2.left 都算啱）
    mode, ref = hit_test(rects, x1 - 2, 25)
    check(f"V3: right edge → {ref}",
          (mode == "right" and ref == (0, 0, 1)) or (mode == "left" and ref == (0, 0, 2)))
    # 空白（lane 之上）
    mode, _ = hit_test(rects, 500, 5)
    check("V3: empty（ruler 區）", mode == "empty")
    # ms 換算
    check("V3: ms_at_x", ms_at_x(100, ppm, 0) == 1000)


# ---------------------------------------------------------------------------
# V4 · undo 完整性（隨機 100 步 → 全 undo → byte-identical）
# ---------------------------------------------------------------------------

def _make_random_op(st: EditorState, rng: random.Random) -> commands.Command | None:
    """根據**當前** doc 狀態生成一個合法隨機 op（indices 以當下為準）。"""
    lines = st.doc["lines"]
    if not lines:
        return commands.AddLine(rng.randint(0, 200_000), rng.randint(200_000, 300_000), "新歌詞")
    op = rng.choice(["char", "shift", "split", "merge", "insert", "add", "delete", "text"])
    line_i = rng.randrange(len(lines))
    line = lines[line_i]
    words = line.get("words") or []
    if op == "char" and words:
        word_i = rng.randrange(len(words))
        chars = words[word_i].get("chars") or []
        if chars:
            char_i = rng.randrange(len(chars))
            c = chars[char_i]
            old = (int(c["begin_ms"]), int(c["end_ms"]))
            new = (max(0, old[0] + rng.randint(-200, 200)), old[1] + rng.randint(0, 100))
            return commands.SetCharTiming(line_i, word_i, char_i, old, new)
        return None
    if op == "shift" and words:
        word_i = rng.randrange(len(words))
        originals = [(int(c["begin_ms"]), int(c["end_ms"])) for c in words[word_i]["chars"]]
        return commands.SetWordShift(line_i, word_i, rng.randint(-300, 300), originals)
    if op == "split" and len(words) >= 2:
        return commands.SplitLine(line_i, rng.randrange(1, len(words)))
    if op == "merge" and line_i + 1 < len(lines):
        return commands.MergeLines(line_i, 2)
    if op == "insert" and words:
        return commands.InsertWord(line_i, rng.randrange(len(words)), "字")
    if op == "add":
        return commands.AddLine(rng.randint(0, 200_000), rng.randint(200_000, 300_000), "新歌詞")
    if op == "delete":
        return commands.DeleteLine(line_i)
    if op == "text" and words:
        text = line.get("text") or ""
        return commands.SetLineText(line_i, text)   # 同長度 → 時間軸不變
    return None


def test_v4_undo_random_100() -> None:
    rng = random.Random(20261030)
    st = EditorState()
    st.doc = json.loads(SMOKE.read_text(encoding="utf-8"))
    original = copy.deepcopy(st.doc)

    # 邊套用邊生成（indices 對當前狀態先有效）
    cmds: list[commands.Command] = []
    for _ in range(300):
        cmd = _make_random_op(st, rng)
        if cmd is not None:
            st.push(cmd)
            cmds.append(cmd)
        if len(cmds) >= 100:
            break
    check(f"V4: 執行咗 {len(cmds)} 步隨機操作", len(cmds) >= 60)

    while st.undo():
        pass
    check("V4: 全 undo 後 doc byte-identical", st.doc == original)

    # redo 全部 → 再全 undo → 仍然一致
    while st.redo():
        pass
    check("V4: redo 全部後唔等於 original（有修改）", st.doc != original)
    while st.undo():
        pass
    check("V4: redo→undo 循環後仍然 byte-identical", st.doc == original)


# ---------------------------------------------------------------------------
# V6 · 正規化（重疊 / 倒轉 / 越界）
# ---------------------------------------------------------------------------

def test_v6_normalize() -> None:
    doc = {
        "source": {"hold_ms": 300},
        "lines": [
            {"start_ms": 0, "end_ms": 5000, "text": "", "words": [{
                "text": "你好", "begin_ms": 0, "end_ms": 1000, "estimated": True,
                "punctuation": "",
                "chars": [{"text": "你", "begin_ms": 800, "end_ms": 500},      # 倒轉
                          {"text": "好", "begin_ms": 400, "end_ms": 300}]},    # 越界
            ]},
            {"start_ms": 1000, "end_ms": 1200, "text": "", "words": [{
                "text": "嗎", "begin_ms": 1000, "end_ms": 1200, "estimated": False,
                "punctuation": "",
                "chars": [{"text": "嗎", "begin_ms": 1000, "end_ms": 1200}]},
            ]},
        ],
    }
    models.normalize(doc)
    a_chars = doc["lines"][0]["words"][0]["chars"]
    check("V6: char 唔倒轉（begin < end）",
          all(c["begin_ms"] < c["end_ms"] for c in a_chars))
    check("V6: char 順序唔倒轉（遞增）",
          all(a_chars[i]["begin_ms"] <= a_chars[i + 1]["begin_ms"]
              for i in range(len(a_chars) - 1)))
    l0, l1 = doc["lines"]
    check("V6: 行同行唔重疊（end ≤ 下一行 start）", int(l0["end_ms"]) <= int(l1["start_ms"]))
    check("V6: N1 text 由 words 重建", doc["lines"][0]["text"] == "你好")
    check("V6: N2 start = 首 char begin", int(l0["start_ms"]) == a_chars[0]["begin_ms"])
    check("V6: schema bump 到 3", doc["schema_version"] == 3)


# ---------------------------------------------------------------------------
# V7 · 斷句 → 合併 → char 時間軸完全還原
# ---------------------------------------------------------------------------

def test_v7_split_merge_roundtrip() -> None:
    st = EditorState()
    st.doc = json.loads(SMOKE.read_text(encoding="utf-8"))
    before = copy.deepcopy(st.doc["lines"])

    # 揾一條 >= 2 個 word 嘅行
    target = next(i for i, l in enumerate(st.doc["lines"]) if len(l["words"]) >= 2)
    st.push(commands.SplitLine(target, 1))
    check("V7: 斷句後行數 +1", len(st.doc["lines"]) == len(before) + 1)
    check("V7: 新行 break_reason = manual", st.doc["lines"][target + 1]["break_reason"] == "manual")

    st.push(commands.MergeLines(target, 2))
    check("V7: 合併後行數還原", len(st.doc["lines"]) == len(before))
    got = [ [ (c["begin_ms"], c["end_ms"]) for w in l["words"] for c in w["chars"] ]
            for l in st.doc["lines"] ]
    want = [ [ (c["begin_ms"], c["end_ms"]) for w in l["words"] for c in w["chars"] ]
             for l in before ]
    check("V7: char 時間軸完全還原", got == want)
    check("V7: 行文字還原", [l.get("text") for l in st.doc["lines"]]
          == [l.get("text") for l in before])


if __name__ == "__main__":
    test_v3_hit_test()
    test_v4_undo_random_100()
    test_v6_normalize()
    test_v7_split_merge_roundtrip()
    print(f"\nALL PASS ({len(PASS)} checks)")
