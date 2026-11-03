# -*- coding: utf-8 -*-
"""9.2 第 3 / 4 項 —— ASR 解析與正規化的邊界行為。

對應 docs/代碼審查報告.html：
- 9.2-3  無歌詞（ASR 空 transcripts）
- 9.2-4  歌詞與音訊語言不一致（OpenCC 路徑）
"""

from __future__ import annotations

from typing import Any

import pytest

import main

from fixtures.builder import build_groups_from_texts


def _asr_payload(sentences: list[dict[str, Any]]) -> dict[str, Any]:
    """組一個最小可用的百煉 filetrans 回傳 JSON。"""
    return {"transcripts": [{"sentences": sentences}]}


def _sentence(text: str, begin: int, end: int, words: list[tuple[str, int, int, str]]) -> dict[str, Any]:
    """組一個 ``sentences[]`` 項目；words 為 ``(text, begin_ms, end_ms, punctuation)``。"""
    return {
        "begin_time": begin,
        "end_time": end,
        "text": text,
        "words": [
            {"text": t, "begin_time": b, "end_time": e, "punctuation": p}
            for (t, b, e, p) in words
        ],
    }


# ---------------------------------------------------------------------------
# 9.2-3 · 無歌詞
# ---------------------------------------------------------------------------


def test_parse_asr_result_empty_transcripts_raises() -> None:
    """``transcripts: []`` → 語意化的 NoSubtitlesError，而非 sys.exit(4)。"""
    with pytest.raises(main.NoSubtitlesError):
        main.parse_asr_result({"transcripts": []})


def test_parse_asr_result_missing_transcripts_key_raises() -> None:
    """完全冇 ``transcripts`` 字段 → 同上（防供應商改 schema 時靜默）。"""
    with pytest.raises(main.NoSubtitlesError):
        main.parse_asr_result({"request_id": "x"})


def test_split_into_lines_empty_groups() -> None:
    """冇任何詞 → 零行（唔可以拋，因為這是合法輸入）。"""
    assert main.split_into_lines([]) == []


def test_build_word_groups_empty_sentences() -> None:
    assert main.build_word_groups([]) == []


def test_parse_asr_result_all_noise_yields_no_sentences() -> None:
    """全部句子都係單字雜訊 → 回空列表（下游 split 亦應得 []）。"""
    data = _asr_payload([
        _sentence("用。", 0, 800, [("用", 0, 700, "。")]),
        _sentence("啊", 900, 1200, [("啊", 900, 1200, "")]),
    ])
    assert main.parse_asr_result(data) == []


# ---------------------------------------------------------------------------
# 9.2-3 附帶 · 正常解析與雜訊過濾
# ---------------------------------------------------------------------------


def test_parse_asr_result_keeps_real_sentence_and_drops_noise() -> None:
    data = _asr_payload([
        _sentence("月光照在", 0, 2000, [("月光", 0, 1000, ""), ("照在", 1000, 2000, "，")]),
        _sentence("用。", 2100, 2600, [("用", 2100, 2500, "。")]),
    ])
    sentences = main.parse_asr_result(data)
    assert len(sentences) == 1
    assert sentences[0].text == "月光照在"
    assert [w.text for w in sentences[0].words] == ["月光", "照在"]
    assert sentences[0].words[-1].punctuation == "，"


def test_parse_asr_result_skips_blank_word_tokens() -> None:
    """API 偶爾回空白 token —— 必須丟棄，否則會多出零時長字。"""
    data = _asr_payload([
        _sentence("月光照在", 0, 2000, [
            ("月光", 0, 1000, ""),
            ("   ", 1000, 1000, ""),
            ("照在", 1000, 2000, "，"),
        ]),
    ])
    words = main.parse_asr_result(data)[0].words
    assert [w.text for w in words] == ["月光", "照在"]


# ---------------------------------------------------------------------------
# 9.2-4 · 語言不一致 / OpenCC
# ---------------------------------------------------------------------------


def test_opencc_skips_pure_latin_content() -> None:
    """純英文歌 → 完全跳過 OpenCC，並回傳**同一個** list（無多餘複製）。"""
    groups = build_groups_from_texts(["hello", "world"])
    assert main.normalize_groups_opencc(groups) is groups


def test_opencc_converts_simplified_to_hongkong() -> None:
    """簡→繁（s2hk）：干什么 → 幹什麼（靠詞組判斷，非逐字轉）。"""
    groups = build_groups_from_texts(["干什么"])
    out = main.normalize_groups_opencc(groups)
    assert "".join(c.text for g in out for c in g.chars) == "幹什麼"


def test_opencc_preserves_char_count_and_timeline() -> None:
    """字數與時間軸必須完全不變，否則逐字高亮會整體錯位。"""
    groups = build_groups_from_texts(["里面", "说说"])
    before = [(c.begin_ms, c.end_ms) for g in groups for c in g.chars]
    out = main.normalize_groups_opencc(groups)
    after = [(c.begin_ms, c.end_ms) for g in out for c in g.chars]
    assert after == before
    assert sum(len(g.chars) for g in out) == sum(len(g.chars) for g in groups)


def test_opencc_returns_new_objects_without_mutating_input() -> None:
    """正規化唔應該原地改動輸入（UI undo stack 依賴舊物件不變）。"""
    groups = build_groups_from_texts(["干什么"])
    original_text = "".join(c.text for g in groups for c in g.chars)
    main.normalize_groups_opencc(groups)
    assert "".join(c.text for g in groups for c in g.chars) == original_text


def test_has_cjk_detection() -> None:
    """ADR-012：只有 CJK 才跑 OpenCC；全形英數唔算 CJK。"""
    assert main.has_cjk("月光") is True
    assert main.has_cjk("hello") is False
    assert main.has_cjk("ＡＢＣ") is False
