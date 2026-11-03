# -*- coding: utf-8 -*-
"""fixtures.builder — 程式生成 timings doc（服侍「大數據量」類測試）。

手寫 fixture（``sample_v3.json``）負責覆蓋行為分支；本模組負責**規模**：
3 小時音訊、5,000 行歌詞這類唔可能手寫嘅情境。

⚠️ 全部時間為整數毫秒（同 generate 一致），確保測試結果可重現、無浮點漂移。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

#: 每行預設字數（與 main.MERGE_MAX_CHARS 同級，屬「正常行」長度）
DEFAULT_CHARS_PER_LINE = 10

STYLE: dict[str, Any] = {
    "play_res_x": 1920,
    "play_res_y": 1080,
    "font_name": "Microsoft JhengHei",
    "font_size": 48,
    "primary_color": "#FFFF00",
    "secondary_color": "#FFFFFF",
    "outline_color": "#000000",
    "alignment": 2,
    "margin_v": 60,
    "outline_width": 3.0,
}

#: 生成用的字池（避免依賴隨機數，結果完全可重現）
_CHAR_POOL = "月光照在夜深了心內有話誰窗外世界時間慢流記憶之中燈熄滅再唱起這首歌"


def _split_evenly(text: str, begin_ms: int, end_ms: int) -> list[dict[str, Any]]:
    """把 ``text`` 平均切成逐字 CharSlot（同 main.word_slot_to_group 的算法一致）。"""
    total = end_ms - begin_ms
    count = len(text)
    chars: list[dict[str, Any]] = []
    cursor = begin_ms
    for i, ch in enumerate(text):
        next_ms = begin_ms + round(total * (i + 1) / count)
        if next_ms <= cursor:
            next_ms = cursor + 1
        chars.append({"text": ch, "begin_ms": cursor, "end_ms": next_ms})
        cursor = next_ms
    return chars


def make_word(
    text: str,
    begin_ms: int,
    end_ms: int,
    *,
    estimated: bool = True,
    punctuation: str = "",
    chars: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """組一個 ``words[]`` 項目。

    Args:
        text: 詞面（純字，唔含標點）。
        begin_ms: 詞起始毫秒。
        end_ms: 詞結束毫秒。
        estimated: 詞內逐字時間是否為估算值。
        punctuation: 詞後標點（ADR-013）。
        chars: 明確指定 chars；``None`` 時按字數平均切。

    Returns:
        timings doc 的 word dict。
    """
    return {
        "text": text,
        "begin_ms": begin_ms,
        "end_ms": end_ms,
        "estimated": estimated,
        "punctuation": punctuation,
        "chars": list(chars) if chars is not None else _split_evenly(text, begin_ms, end_ms),
    }


def make_line(
    words: Sequence[dict[str, Any]],
    *,
    hold_ms: int = 0,
    break_reason: str = "punctuation",
) -> dict[str, Any]:
    """組一個 ``lines[]`` 項目（start/end 由 words 推導，可加 hold）。"""
    start = words[0]["begin_ms"]
    end = words[-1]["end_ms"] + hold_ms
    return {
        "start_ms": start,
        "end_ms": end,
        "text": "".join(w["text"] for w in words),
        "break_reason": break_reason,
        "words": list(words),
    }


def _line_text(seed: int, length: int) -> str:
    """由 seed 取一段可重現的字串（循環字池）。"""
    return "".join(_CHAR_POOL[(seed * 7 + i) % len(_CHAR_POOL)] for i in range(length))


def build_doc(
    num_lines: int = 20,
    *,
    line_ms: int = 3600,
    gap_ms: int = 100,
    chars_per_line: int = DEFAULT_CHARS_PER_LINE,
    words_per_line: int = 2,
) -> dict[str, Any]:
    """生成一個合法 schema v3 doc。

    時間軸嚴格遞增且無重疊（``line.end = start + line_ms``，下一行 ``+gap_ms``），
    因此可直接餵給 ``timings_doc_to_lines`` / ``build_karaoke_ass``。

    Args:
        num_lines: 行數。
        line_ms: 每行佔用時長（ms）。
        gap_ms: 行與行之間的空白（ms）。
        chars_per_line: 每行字數。
        words_per_line: 每行拆成幾個詞（須能整除 ``chars_per_line``）。

    Returns:
        timings doc（schema_version=3）。
    """
    if words_per_line < 1:
        raise ValueError("words_per_line 必須 >= 1")
    per_word = max(1, chars_per_line // words_per_line)
    lines: list[dict[str, Any]] = []
    cursor = 0
    for i in range(num_lines):
        word_ms = line_ms // words_per_line
        words: list[dict[str, Any]] = []
        for w in range(words_per_line):
            b = cursor + w * word_ms
            e = b + word_ms
            words.append(make_word(_line_text(i * 3 + w, per_word), b, e))
        # 最後一個詞補回除不盡的餘數，令 line.end 對齊 cursor + line_ms
        words[-1]["end_ms"] = cursor + line_ms
        words[-1]["chars"] = _split_evenly(words[-1]["text"], words[-1]["begin_ms"],
                                          words[-1]["end_ms"])
        words[-1]["punctuation"] = "。" if i % 2 == 0 else ""
        lines.append(make_line(words, break_reason="punctuation" if i % 2 == 0 else "max_chars"))
        cursor += line_ms + gap_ms

    return {
        "schema_version": 3,
        "source": {
            "video": "tests/fixtures/__generated__.mp4",
            "video_width": 1920,
            "video_height": 1080,
            "model": "qwen-audio-3.1-asr-flash-filetrans",
            "hold_ms": 300,
            "generated_at": "2026-11-03T00:00:00+00:00",
            "_note": "fixtures.builder.build_doc 生成，非真實素材",
        },
        "style": dict(STYLE),
        "lines": lines,
    }


def build_long_audio_doc(duration_ms: int = 3 * 60 * 60 * 1000) -> dict[str, Any]:
    """生成足以覆蓋 ``duration_ms`` 的 doc（9.2 第 6 項「超長音訊」）。

    Args:
        duration_ms: 目標總時長，預設 3 小時。

    Returns:
        timings doc；行數 = ``duration_ms // 3700``（3600 行長 + 100 行距）。
    """
    num_lines = max(1, duration_ms // 3700)
    return build_doc(num_lines)


def build_groups_from_texts(texts: Iterable[str], *, char_ms: int = 300) -> list[Any]:
    """由純文字列表造 ``main.WordGroup`` 列表（給斷句 / 渲染類測試用）。

    Args:
        texts: 每項一個詞（會逐字切）。
        char_ms: 每字時長。

    Returns:
        ``main.WordGroup`` 列表；每個詞的 ``punctuation`` 為空。
    """
    import main

    groups = []
    cursor = 0
    for text in texts:
        end = cursor + char_ms * len(text)
        groups.append(main.word_slot_to_group(main.WordSlot(text=text, begin_ms=cursor, end_ms=end)))
        cursor = end
    return groups
