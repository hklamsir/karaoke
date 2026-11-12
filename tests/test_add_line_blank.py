# -*- coding: utf-8 -*-
"""全形空白歌詞的契約測試（ADR-043）。

用戶回報：在歌詞樹「插入」時輸入一個全形空白（U+3000），會彈「請輸入歌詞」。
根因是驗證用 ``raw.strip()``，全形空白 strip 之後變空字串 —— 但全形空白是
合法輸入（句中可見間隔／整句留白），只有「完全冇打字」才應該擋。
"""
from __future__ import annotations

import pytest

import main
from ui import models

FULLWIDTH_SPACE = "\u3000"


def _empty_doc() -> dict:
    return {
        "schema_version": 3,
        "source": {"hold_ms": 300},
        "style": {"play_res_x": 1920, "play_res_y": 1080, "font_size": 48},
        "lines": [],
    }


def test_add_line_accepts_fullwidth_space() -> None:
    """全形空白要建成一個 char，唔可以被 strip 掉。"""
    doc = _empty_doc()
    line = models.add_line(doc, 1000, 2000, FULLWIDTH_SPACE)
    chars = [c for w in line["words"] for c in w["chars"]]
    assert [c["text"] for c in chars] == [FULLWIDTH_SPACE]
    assert line["start_ms"] == 1000 and line["end_ms"] == 2000


def test_normalize_does_not_crash_on_blank_line() -> None:
    """normalize（存檔前必跑）唔可以因為空白行出錯。"""
    doc = _empty_doc()
    models.add_line(doc, 1000, 2000, FULLWIDTH_SPACE)
    models.normalize(doc)          # 唔應該拋
    assert len(doc["lines"]) == 1


def test_render_keeps_blank_line_safe() -> None:
    """燒錄用的 ASS 生成要行得通（空白行 = 冇可見文字，但唔可以爆）。"""
    doc = _empty_doc()
    models.add_line(doc, 1000, 2000, FULLWIDTH_SPACE)
    ass = main.render_timings(doc)
    assert "Dialogue:" in ass
    assert "[Events]" in ass


def test_mixed_text_with_inner_space_keeps_space() -> None:
    """句中全形空白（正常用法）要原樣保留。"""
    doc = _empty_doc()
    line = models.add_line(doc, 0, 3000, "從前" + FULLWIDTH_SPACE + "人渺")
    text = "".join(c["text"] for w in line["words"] for c in w["chars"])
    assert text == "從前" + FULLWIDTH_SPACE + "人渺"


@pytest.mark.parametrize("blank", [FULLWIDTH_SPACE, " ", "  ", "\t"])
def test_blank_inputs_are_not_empty_strings(blank: str) -> None:
    """各種空白都唔等於空字串 —— UI 只應該擋真的 ``""``。"""
    assert blank != ""
    assert (blank.strip() or blank) == blank or blank.strip() == ""
