# -*- coding: utf-8 -*-
"""9.2 第 5 項（時間戳重疊 / 倒轉）+ §9.3 管線黃金測試 —— ASS 渲染。

對應 docs/代碼審查報告.html §9.3：
- render 兩次輸出 byte-identical（canonical 設計的核心承諾）
- ADR-005 時間格式 ``H:MM:SS.cc``
- 行與行不重疊（N5）
- 逐行 ``\\k`` + ``\\kf`` 總和 ≈ 行時長（鎖住「停頓雙倍」R6 回歸）
"""

from __future__ import annotations

import copy
import re
from typing import Any

import pytest

import main
from ui import models

KF_RE = re.compile(r"\\kf(\d+)")
K_RE = re.compile(r"\\k(\d+)\}")


# ---------------------------------------------------------------------------
# §9.3 黃金測試
# ---------------------------------------------------------------------------


def test_render_is_deterministic(doc: dict[str, Any]) -> None:
    """同一份 doc 渲染兩次必須 byte-identical（浮點漂移 / 字典序不穩的哨兵）。"""
    lines_a, style_a = main.timings_doc_to_lines(doc)
    lines_b, style_b = main.timings_doc_to_lines(copy.deepcopy(doc))
    assert main.build_karaoke_ass(lines_a, style_a) == main.build_karaoke_ass(lines_b, style_b)


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (0, "0:00:00.00"),
        (10, "0:00:00.01"),
        (1_000, "0:00:01.00"),
        (28_000, "0:00:28.00"),
        (3_723_950, "1:02:03.95"),
        # ⚠️ 59_999ms = 5999.9cs → round 成 6000cs = 60.00s（進位到下一分鐘，正確行為）
        (59_999, "0:01:00.00"),
        (59_994, "0:00:59.99"),
        (3_600_000, "1:00:00.00"),
    ],
)
def test_ass_time_format_is_h_mm_ss_cc(ms: int, expected: str) -> None:
    """ADR-005 回歸鎖：格式必須 ``H:MM:SS.cc``。

    寫成 ``M:SS:cc`` 時 libass 會靜默解析錯誤 → 輸出無字幕影片，且 ffmpeg 不報錯。
    """
    assert main.ms_to_ass_time(ms) == expected


def test_ms_to_cs_never_returns_zero() -> None:
    """``\\k`` 參數必須 ≥ 1cs，否則 libass 會吞掉該字。"""
    assert main.ms_to_cs(0) == 1
    assert main.ms_to_cs(4) == 1
    assert main.ms_to_cs(10) == 1
    assert main.ms_to_cs(15) == 2


def test_dialogue_count_matches_lines(doc: dict[str, Any], dialogues) -> None:
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style))
    assert len(events) == len(lines) == len(doc["lines"])


def test_no_dialogue_overlap(doc: dict[str, Any], dialogues) -> None:
    """行與行唔可以重疊（容差 310ms = hold_ms 300 + 10ms 緩衝）。"""
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style))
    for a, b in zip(events, events[1:]):
        assert a["end_ms"] <= b["start_ms"] + 310, f"重疊：{a['raw']} / {b['raw']}"


def test_kf_total_matches_line_duration(doc: dict[str, Any], dialogues) -> None:
    """逐行 ``\\k`` + ``\\kf`` 總和 ≈ (end - start)/10 cs。

    這條鎖住 R6 的「停頓雙倍」：若 C-5 停頓補償重複計同一段 gap，
    總和會多出「gap 的 cs 數」，遠超容差。
    """
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style))

    for line, event in zip(lines, events):
        text = event["text"]
        total_cs = sum(int(x) for x in KF_RE.findall(text)) + sum(int(x) for x in K_RE.findall(text))
        expected_cs = round((line.end_ms - line.start_ms) / 10)
        n_chars = sum(len(w.chars) for w in line.words)
        # 每個分段各自 round，累積誤差上限約 n_chars/2；再加 2cs 寬鬆
        tolerance = n_chars // 2 + 2
        assert abs(total_cs - expected_cs) <= tolerance, (
            f"{line.text!r}: \\k 總和 {total_cs}cs，預期 {expected_cs}cs（容差 {tolerance}）"
        )


def test_header_declares_playres_and_style(doc: dict[str, Any]) -> None:
    """ADR-006：PlayRes 由原片讀出，必須出現在 header；Style 行必須存在。"""
    lines, style = main.timings_doc_to_lines(doc)
    ass = main.build_karaoke_ass(lines, style)
    assert "PlayResX: 1920" in ass
    assert "PlayResY: 1080" in ass
    assert "ScaledBorderAndShadow: yes" in ass
    assert ass.startswith("[Script Info]")
    assert "Style: Karaoke," in ass


def test_style_colors_are_bgr(doc: dict[str, Any]) -> None:
    lines, style = main.timings_doc_to_lines(doc)
    ass = main.build_karaoke_ass(lines, style)
    assert "&H0000FFFF" in ass      # primary #FFFF00
    assert "&H00FFFFFF" in ass      # secondary #FFFFFF


# ---------------------------------------------------------------------------
# 9.2-5 · 時間戳重疊 / 倒轉
# ---------------------------------------------------------------------------


def _corrupt_doc(base: dict[str, Any]) -> dict[str, Any]:
    """把 fixture 的第 2 行改成「行時間倒轉 + char 次序倒轉」。"""
    doc = copy.deepcopy(base)
    line = doc["lines"][1]
    line["start_ms"], line["end_ms"] = 6000, 5000
    chars = line["words"][0]["chars"]
    chars[0]["begin_ms"], chars[0]["end_ms"] = 5200, 5100
    return doc


def test_render_never_emits_nonpositive_kf(doc: dict[str, Any], dialogues) -> None:
    """壞資料直接渲染（未 normalize）→ 所有 ``\\k`` / ``\\kf`` 參數必須 ≥ 1。

    唔要求修好時間（那是 normalize 的職責），只要求渲染層**唔會**輸出
    ``\\kf-3`` 這種令 libass 行為未定義的參數。
    """
    lines, style = main.timings_doc_to_lines(_corrupt_doc(doc))
    events = dialogues(main.build_karaoke_ass(lines, style))
    for event in events:
        for value in KF_RE.findall(event["text"]) + K_RE.findall(event["text"]):
            assert int(value) >= 1, event["raw"]


def test_normalize_fixes_reversed_and_overlapping(doc: dict[str, Any], dialogues) -> None:
    """normalize 後：char 遞增、行唔重疊、渲染出的 Dialogue start < end。"""
    bad = _corrupt_doc(doc)
    bad["lines"][2]["start_ms"] = 9100
    bad["lines"][2]["end_ms"] = 13000          # 與第 2 行重疊
    models.normalize(bad)

    for line in bad["lines"]:
        begins = [c["begin_ms"] for w in line["words"] for c in w["chars"]]
        assert begins == sorted(begins)
        assert all(int(c["end_ms"]) > int(c["begin_ms"])
                   for w in line["words"] for c in w["chars"])

    lines, style = main.timings_doc_to_lines(bad)
    events = dialogues(main.build_karaoke_ass(lines, style))
    for a, b in zip(events, events[1:]):
        assert a["end_ms"] <= b["start_ms"], f"normalize 後仍重疊：{a['raw']} / {b['raw']}"
    for event in events:
        assert event["start_ms"] < event["end_ms"], event["raw"]


# ---------------------------------------------------------------------------
# 標點策略（ADR-013 / --keep-punctuation）
# ---------------------------------------------------------------------------


def test_strip_punctuation_default_turns_mid_punct_into_space(doc: dict[str, Any], dialogues) -> None:
    """行中標點 → 半形空格（防雙空格）；行尾標點 → 剝走。"""
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style, strip_punctuation=True))
    # 第 6 行 fixture：搖滾 / 全形🤘（，） / 吧（！） → 行中變空格、行尾剝走
    text = events[5]["text"]
    assert "，" not in text and "！" not in text
    assert "  " not in text, "唔可以出現雙空格"


def test_keep_punctuation_preserves_marks(doc: dict[str, Any], dialogues) -> None:
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style, strip_punctuation=False))
    assert "，" in events[5]["text"]
    assert "！" in events[5]["text"]
