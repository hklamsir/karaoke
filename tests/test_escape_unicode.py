# -*- coding: utf-8 -*-
"""9.2 第 8 項 + §2.7 —— Unicode 歌詞與 ASS 跳脫。

對應 docs/代碼審查報告.html：
- 9.2-8  Unicode 歌詞（emoji / CJK / 全形）
- §2.7   ``escape_ass_text`` 未跳脫反斜線 → override tag 注入
- §4.4   歌詞含 ``\\n`` 未轉成 ASS 的 ``\\N`` 換行
"""

from __future__ import annotations

import pytest

import main


# ---------------------------------------------------------------------------
# 括號跳脫（現行已正確，回歸鎖）
# ---------------------------------------------------------------------------


def test_escape_braces() -> None:
    """``{}`` 是 ASS 的 override block 界定符，必須跳脫，否則整段被當指令。"""
    assert main.escape_ass_text("{搖滾}") == "\\{搖滾\\}"


def test_escape_plain_text_unchanged() -> None:
    assert main.escape_ass_text("月光照在") == "月光照在"
    assert main.escape_ass_text("hello world") == "hello world"


# ---------------------------------------------------------------------------
# §2.7 · 反斜線注入（待修）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 反斜線 / 換行
# ---------------------------------------------------------------------------


def test_escape_backslash_is_doubled() -> None:
    """反斜線必須雙寫（鎖住確切字串，防日後有人「順手簡化」）。

    注意實際防護範圍（2026-11-03 用真 libass 量測過）：
    - override tag 只在大括號內生效，而大括號本身已跳脫 → 呢步唔係擋 tag 的關鍵
    - ``\\N`` 在大括號外亦生效，雙寫**唔能**阻止它換行（``AB\\\\NCD`` 仍渲染兩行）
    所以這步是「保守處理」，唔係萬能；真正關鍵是跳脫大括號。
    """
    out = main.escape_ass_text("{搖滾}🤘\\kf")
    assert out == "\\{搖滾\\}🤘\\\\kf"


def test_escape_newline_to_ass_linebreak() -> None:
    """含 ``\\n`` 的歌詞應轉成 ``\\N``（ASS 硬換行）。"""
    assert main.escape_ass_text("a\nb") == "a\\Nb"


def test_escape_crlf_becomes_single_linebreak() -> None:
    """``\\r\\n`` 必須只產生**一個** ``\\N`` —— 先處理 CRLF 才處理單獨字元。"""
    assert main.escape_ass_text("a\r\nb") == "a\\Nb"
    assert main.escape_ass_text("a\rb") == "a\\Nb"


def test_escape_lone_backslash_unchanged_count() -> None:
    """單一反斜線 → 兩個反斜線（逐字對照，避免連鎖替換）。"""
    assert main.escape_ass_text("\\") == "\\\\"
    assert main.escape_ass_text("C:\\MV") == "C:\\\\MV"


# ---------------------------------------------------------------------------
# emoji / 多 codepoint
# ---------------------------------------------------------------------------


def test_single_emoji_word_is_not_split() -> None:
    """單個 emoji（1 codepoint）→ 1 個 CharSlot、``estimated=False``。"""
    group = main.word_slot_to_group(main.WordSlot("🤘", 0, 900))
    assert len(group.chars) == 1
    assert group.estimated is False
    assert group.chars[0].text == "🤘"


def test_multi_emoji_word_splits_by_codepoint() -> None:
    """兩個 emoji → 按 codepoint 切 2 份並標 estimated（時間為估算）。"""
    group = main.word_slot_to_group(main.WordSlot("🤘🎤", 0, 1000))
    assert [c.text for c in group.chars] == ["🤘", "🎤"]
    assert group.estimated is True
    assert group.chars[0].begin_ms == 0
    assert group.chars[-1].end_ms == 1000


def test_pure_latin_word_is_not_split() -> None:
    """純拉丁詞整體保留（避免逐字母閃動）。"""
    group = main.word_slot_to_group(main.WordSlot("hello", 0, 1100))
    assert len(group.chars) == 1
    assert group.estimated is False


def test_mixed_cjk_latin_word_splits_by_codepoint() -> None:
    """中英混合詞唔符合純拉丁特例 → 逐 codepoint 切（現行行為，回歸鎖）。"""
    group = main.word_slot_to_group(main.WordSlot("ABC月光", 0, 500))
    assert [c.text for c in group.chars] == ["A", "B", "C", "月", "光"]
    assert group.estimated is True


# ---------------------------------------------------------------------------
# 端到端：Unicode 唔可以令渲染崩或漏字
# ---------------------------------------------------------------------------


def test_emoji_survives_render(doc, dialogues) -> None:
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style))
    joined = "".join(e["text"] for e in events)
    assert "🤘" in joined
    assert "hello" in joined


def test_unicode_char_count_matches_timeline(doc, dialogues) -> None:
    """每個 CharSlot 都必須產生恰好一個 ``\\kf``（唔可以漏字或重複）。"""
    lines, style = main.timings_doc_to_lines(doc)
    events = dialogues(main.build_karaoke_ass(lines, style))
    for line, event in zip(lines, events):
        n_chars = sum(len(w.chars) for w in line.words)
        assert event["text"].count("\\kf") == n_chars
