# -*- coding: utf-8 -*-
"""ADR-031 契約測試：歌詞提前顯示 + 倒數圓點。

鎖住五件事：
1. 長間奏唔會提早幾十秒顯示下一句（最多 ``lead_in_ms``）
2. 句尾停留（換行延遲）唔可以蓋過下一句開唱，同排永不疊字
3. 倒數三點喺 -1.5/-1.0/-0.5 秒逐點亮起，最後一點亮到開唱
4. 密句（上一句一走下一句即到）唔會出圓點
5. ``lead_in_ms=0`` 完全回到 ADR-027 舊行為
"""
from __future__ import annotations

import pytest

import main


def _make_doc(starts: list[int], *, dual: bool = False, lead_in: int = 3000,
              dots: bool = True, delay: int = 1000, hold: int = 2000,
              alignment: int = 2, dot_gap: int = 5000) -> dict:
    """每句固定長 ``hold`` ms，句與句之間可以有空隙。"""
    lines = []
    for i, start in enumerate(starts):
        end = start + hold
        text = f"第{i}句"
        step = hold // len(text)
        lines.append({
            "start_ms": start, "end_ms": end, "text": text,
            "break_reason": "punctuation",
            # 一個 word、三個 char —— 同真實 timings 一樣：word.text == chars 串接，
            # 否則 display_text 同實際渲染文字唔同，圓點影子尾巴會算錯闊度
            "words": [{"text": text, "punctuation": "", "estimated": False,
                       "chars": [{"text": ch,
                                  "begin_ms": start + j * step,
                                  "end_ms": start + (j + 1) * step}
                                 for j, ch in enumerate(text)]}],
        })
    style = {
        "play_res_x": 1920, "play_res_y": 1080,
        "font_size": 100, "margin_v": 100, "outline_width": 4.0,
        "lead_in_ms": lead_in, "countdown_dots": dots,
        "dot_min_gap_ms": dot_gap,
        "switch_delay_ms": delay, "alignment": alignment,
    }
    if dual:
        style["dual_line"] = True
    return {"schema_version": 3, "source": {}, "style": style, "lines": lines}


def _lyrics(ass: str) -> list[str]:
    """Layer 0 = 歌詞 event。"""
    return [ln for ln in ass.splitlines()
            if ln.startswith("Dialogue: 0")]


def _dots(ass: str) -> list[str]:
    """Layer 1 = 倒數圓點 event。"""
    return [ln for ln in ass.splitlines()
            if ln.startswith("Dialogue: 1")]


def _times(event: str) -> tuple[str, str]:
    parts = event.split(",", 10)
    return parts[1], parts[2]


def _text(event: str) -> str:
    return event.split(",", 9)[9]


def _lit_dots(event: str) -> int:
    """已亮圓點數 = ``{\\alpha&HFF&}``（隱形段）之前嘅「●」數目。"""
    return _text(event).split("{\\alpha&HFF&}")[0].count("●")


# ---------------------------------------------------------------------------
# 提前顯示
# ---------------------------------------------------------------------------

def test_long_interlude_caps_lead_in() -> None:
    """20 秒空白：下一句只喺開唱前 3 秒出現，唔係提早 20 秒。"""
    ass = main.render_timings(_make_doc([0, 20_000]))
    got = [_times(e) for e in _lyrics(ass)]
    assert got[1][0] == main.ms_to_ass_time(17_000)      # 20.0 − 3.0
    assert got[1][1] == main.ms_to_ass_time(23_000)      # 句尾停留 1 秒


def test_lead_in_zero_restores_legacy_window() -> None:
    """lead_in_ms=0：窗口 = 句子本身（舊行為）。"""
    ass = main.render_timings(_make_doc([0, 20_000], lead_in=0))
    assert _times(_lyrics(ass)[1]) == (main.ms_to_ass_time(20_000),
                                       main.ms_to_ass_time(22_000))
    assert _dots(ass) == []


def test_tail_hold_never_swallows_next_line() -> None:
    """句尾停留唔可以拖到下一句已經開唱（否則同一行兩個 event 疊字）。"""
    # 句距 1.2s，句尾停留 1s → 仍然要喺下一句開唱前清空
    ass = main.render_timings(_make_doc([0, 1200], hold=800))
    e0 = _times(_lyrics(ass)[0])
    assert e0[1] == main.ms_to_ass_time(1200)


def test_same_row_never_overlaps_with_lead_in() -> None:
    """雙行 + 提前顯示：同一行（相隔兩句）嘅 event 唔可以重疊。"""
    ass = main.render_timings(_make_doc([0, 1000, 2000, 3000], dual=True,
                                        hold=2500))
    rows = {0: [], 1: []}
    for i, event in enumerate(_lyrics(ass)):
        rows[i % 2].append(_times(event))
    for row in rows.values():
        for (_s0, e0), (s1, _e1) in zip(row, row[1:]):
            assert e0 <= s1


def test_karaoke_highlight_waits_for_lead_in() -> None:
    """提前出現嘅 event 要用前置 \\k 佔住時間軸，否則高亮會早咗起跑。"""
    ass = main.render_timings(_make_doc([0, 20_000]))
    body = _text(_lyrics(ass)[1])
    assert "{\\k300}" in body        # 3 秒 = 300 厘秒


# ---------------------------------------------------------------------------
# 倒數圓點
# ---------------------------------------------------------------------------

def test_countdown_dots_appear_one_by_one() -> None:
    """三點喺 -1.5 / -1.0 / -0.5 秒逐個亮起，第三點亮到開唱一刻為止。"""
    ass = main.render_timings(_make_doc([0, 20_000]))
    dots = _dots(ass)
    assert len(dots) == 3
    assert [_times(e) for e in dots] == [
        (main.ms_to_ass_time(18_500), main.ms_to_ass_time(19_000)),
        (main.ms_to_ass_time(19_000), main.ms_to_ass_time(19_500)),
        (main.ms_to_ass_time(19_500), main.ms_to_ass_time(20_000)),
    ]
    # 每次只多亮一粒：`{\alpha&HFF&}` 之前嘅「●」= 已亮數目
    assert [_lit_dots(e) for e in dots] == [1, 2, 3]


def test_dots_align_to_first_char_by_width_matching() -> None:
    """圓點事件用「3 粒點 + 隱形歌詞尾巴」拼成，總寬度 = 原句 → 左邊自然對齊第一個字。

    關鍵不變量（唔靠估字型度量）：
      - 圓點字級 = 字級 ÷ 3（3 粒點剛好佔一個全形字位）
      - 影子尾巴 = 原文去掉第一個字（嗰個字位讓畀圓點）
      - 事件 ``\\an2`` 同歌詞用同一個 x → 置中後左邊界一致
    """
    ass = main.render_timings(_make_doc([0, 20_000]))
    dot = _dots(ass)[0]
    assert "\\c&H" not in dot                 # 用 Style 的唱過色（唔覆寫）
    assert "\\an2" in dot
    assert "\\fs33" in dot                    # 100 ÷ 3
    assert "\\pos(960," in dot                # 同歌詞同一個中心（雙行下行／單行都是 960）
    assert _text(dot).endswith("句")          # 3 字歌詞去掉第一個字 → 尾巴「句」
    assert "{\\alpha&HFF&}" in dot            # 尾巴隱形


def test_dot_shadow_tail_matches_lyric_text() -> None:
    """影子尾巴必須等於歌詞原文去掉第一個字（否則寬度唔同、又會偏離）。"""
    text = "始終都會海角重遇你"
    doc = _make_doc([0, 20_000])
    doc["lines"][1]["words"] = [{
        "text": "字", "punctuation": "", "estimated": False,
        "chars": [{"text": ch, "begin_ms": 20_000 + i * 500,
                   "end_ms": 20_500 + i * 500} for i, ch in enumerate(text)],
    }]
    dot = _text(_dots(main.render_timings(doc))[0])
    assert dot.endswith(text[1:])


def test_dot_events_all_have_same_width() -> None:
    """三條 event（1/2/3 粒亮點）的文字總長必須一致，否則第一粒會左右跳。

    「亮點 + 未亮（隱形）點」恆為 3 粒 = 一個字位；影子尾巴三條都一樣。
    """
    dots = [_text(e) for e in _dots(main.render_timings(_make_doc([0, 20_000])))]
    assert [d.count("●") for d in dots] == [3, 3, 3]      # 總點數（含隱形）恆定
    assert [t.split("{\\alpha&HFF&}")[0].count("●") for t in dots] == [1, 2, 3]
    # 影子尾巴（`\fs100}` 之後）三條都要一模一樣
    assert [d.split("\\fs100}")[-1] for d in dots] == ["1句"] * 3


def test_dots_need_long_blank_gap() -> None:
    """句距只有 2.5 秒（< 5 秒門檻）→ 唔出圓點，即使有完整空白。"""
    ass = main.render_timings(_make_doc([0, 4500]))     # 句尾 2000 → 空白 2500ms
    assert _dots(ass) == []


def test_dots_threshold_is_configurable() -> None:
    """門檻調到 2 秒 → 同一份時間軸就出圓點；調到 20 秒 → 唔出。"""
    assert _dots(main.render_timings(_make_doc([0, 4500], dot_gap=2000)))
    assert _dots(main.render_timings(_make_doc([0, 20_000], dot_gap=20_000))) == []


def test_dots_threshold_zero_means_any_blank() -> None:
    """門檻 0 = 只要有一段完整空白就出（回復第一版行為）。"""
    assert _dots(main.render_timings(_make_doc([0, 4500], dot_gap=0)))


def test_no_dots_when_lines_are_dense() -> None:
    """密句：上一句一走下一句即到，冇空白就唔應該出圓點。"""
    ass = main.render_timings(_make_doc([0, 2500, 5000], hold=2400))
    assert _dots(ass) == []


def test_dots_can_be_disabled() -> None:
    ass = main.render_timings(_make_doc([0, 20_000], dots=False))
    assert _dots(ass) == []


def test_dots_below_when_top_aligned() -> None:
    """頂部對齊時上方冇位，圓點改放行下方，唔會出畫面。"""
    ass = main.render_timings(_make_doc([0, 20_000], alignment=8))
    dot = _dots(ass)[0]
    # 文字底邊 100 + 字級 100，再向下 0.70 字級 → 270
    assert "\\pos(960,270)" in dot
    assert "\\an2" in dot


# ---------------------------------------------------------------------------
# roundtrip
# ---------------------------------------------------------------------------

def test_lead_in_roundtrip() -> None:
    """lead_in_ms / countdown_dots / dot_min_gap_ms 要寫入 json 並讀返。"""
    doc = _make_doc([0, 20_000], lead_in=1500, dots=False, dot_gap=8000)
    lines, style = main.timings_doc_to_lines(doc)
    saved = main.lines_to_timings_doc(lines, style, doc["source"])
    assert saved["style"]["lead_in_ms"] == 1500
    assert saved["style"]["countdown_dots"] is False
    assert saved["style"]["dot_min_gap_ms"] == 8000

    again, style2 = main.timings_doc_to_lines(saved)
    assert style2.lead_in_ms == 1500
    assert style2.countdown_dots is False
    assert style2.dot_min_gap_ms == 8000


def test_default_lead_in_is_three_seconds() -> None:
    """舊 timings.json 冇 lead_in_ms → 用新預設 3 秒（唔會變 0）。"""
    doc = _make_doc([0, 20_000])
    doc["style"].pop("lead_in_ms")
    doc["style"].pop("countdown_dots")
    _lines, style = main.timings_doc_to_lines(doc)
    assert style.lead_in_ms == 3000
    assert style.countdown_dots is True


def test_explicit_zero_is_not_treated_as_missing() -> None:
    """明寫 lead_in_ms=0 要當「關閉」，唔可以被 `or` 吞成預設值。"""
    doc = _make_doc([0, 20_000], lead_in=0)
    _lines, style = main.timings_doc_to_lines(doc)
    assert style.lead_in_ms == 0


def test_default_style_does_not_write_new_keys() -> None:
    """預設樣式唔會寫新 key，舊檔輸出維持 byte-identical。"""
    lines, style = main.timings_doc_to_lines(_make_doc([0, 20_000]))
    saved = main.lines_to_timings_doc(lines, style, {})
    assert "lead_in_ms" not in saved["style"]
    assert "countdown_dots" not in saved["style"]
