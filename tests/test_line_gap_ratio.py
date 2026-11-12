# -*- coding: utf-8 -*-
"""ADR-039 雙行行距倍數契約測試。

鎖住四件事：
1. 冇設定時仍然是 1.45 × 字級（舊檔 byte-identical）
2. 倍數隨字級縮放（唔會「換字級就變鬆」）
3. px 值優先於倍數（舊 timings.json 只有 px，行為不變）
4. 倍數會寫入／讀回 timings.json，並且真的改變 \\pos 的 y
"""
from __future__ import annotations

import main


def _doc(*, font_size: int = 100, ratio: float | None = None,
         line_gap: int = 0) -> dict:
    """兩句雙行 timings；``ratio=None`` = 完全唔寫倍數（模擬舊檔）。"""
    lines = []
    for i in range(2):
        start = i * 3000
        lines.append({
            "start_ms": start,
            "end_ms": start + 2800,
            "text": f"第{i}句",
            "break_reason": "punctuation",
            "words": [{"text": "第", "punctuation": "", "estimated": False,
                       "chars": [{"text": "第", "begin_ms": start,
                                  "end_ms": start + 900}]}],
        })
    style: dict = {
        "play_res_x": 1920, "play_res_y": 1080, "font_size": font_size,
        "margin_v": 100, "outline_width": 4.0, "dual_line": True,
        "switch_delay_ms": 1000, "lead_in_ms": 0,
    }
    if ratio is not None:
        style["line_gap_ratio"] = ratio
    if line_gap:
        style["line_gap"] = line_gap
    return {"schema_version": 3, "source": {}, "style": style, "lines": lines}


def _pos_y(ass: str, index: int) -> int:
    """抽第 index 個 Dialogue 的 ``\\pos(x,y)`` y 值。"""
    events = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    marker = "\\pos("
    start = events[index].index(marker) + len(marker)
    return int(events[index][start:events[index].index(")", start)].split(",")[1])


# ---------------------------------------------------------------------------
# StyleConfig 層
# ---------------------------------------------------------------------------

def test_default_gap_is_ratio_145() -> None:
    """冇任何設定 → 1.45 × 字級（48px → 70px，同 ADR-027 完全一致）。"""
    assert main.StyleConfig(font_size=48).resolved().line_gap == 70


def test_ratio_scales_with_font_size() -> None:
    """倍數 × 字級：換字級時行距按比例跟隨，唔會變鬆。"""
    assert main.StyleConfig(font_size=48, line_gap_ratio=1.2).resolved().line_gap == 58
    assert main.StyleConfig(font_size=60, line_gap_ratio=1.2).resolved().line_gap == 72


def test_px_wins_over_ratio() -> None:
    """舊 timings.json 只寫 px → px 必須優先，否則升級後行距會突變。"""
    resolved = main.StyleConfig(font_size=48, line_gap=99, line_gap_ratio=1.2).resolved()
    assert resolved.line_gap == 99


def test_ratio_is_idempotent() -> None:
    """``resolved()`` 連跑兩次結果要一樣（避免 replace 之後再算一次就變）。"""
    once = main.StyleConfig(font_size=48, line_gap_ratio=1.3).resolved()
    assert once.resolved().line_gap == once.line_gap


# ---------------------------------------------------------------------------
# timings.json 往返
# ---------------------------------------------------------------------------

def test_ratio_round_trips() -> None:
    style = main.StyleConfig(font_size=48, dual_line=True, line_gap_ratio=1.25)
    doc = main.lines_to_timings_doc([], style, {})
    assert doc["style"]["line_gap_ratio"] == 1.25

    _, back = main.timings_doc_to_lines(doc)
    assert back.line_gap_ratio == 1.25
    assert back.resolved().line_gap == 60          # 48 × 1.25


def test_absent_ratio_adds_no_key() -> None:
    """冇設倍數就唔應該寫 key —— 舊檔輸出維持 byte-identical。"""
    doc = main.lines_to_timings_doc([], main.StyleConfig(dual_line=True), {})
    assert "line_gap_ratio" not in doc["style"]


def test_bad_ratio_value_falls_back_to_zero() -> None:
    """人手改壞 JSON（"abc"）唔應該令開檔失敗，亦唔可以變負行距。"""
    doc = _doc()
    doc["style"]["line_gap_ratio"] = "abc"
    _, style = main.timings_doc_to_lines(doc)
    assert style.line_gap_ratio == 0.0
    assert style.resolved().line_gap == round(100 * 1.45)


# ---------------------------------------------------------------------------
# 真的影響燒錄座標
# ---------------------------------------------------------------------------

def test_ratio_moves_upper_row() -> None:
    """ratio=1.0 → 上行 = 下行 − 100（字級）；ratio=2.0 → 減 200。"""
    tight = main.render_timings(_doc(font_size=100, ratio=1.0))
    loose = main.render_timings(_doc(font_size=100, ratio=2.0))
    down_y = 1080 - 100                          # PlayResY − margin_v
    assert _pos_y(tight, 0) == down_y - 100
    assert _pos_y(loose, 0) == down_y - 200


def test_default_doc_matches_old_formula() -> None:
    """舊檔（冇倍數、冇 px）渲染出來的 y 同 ADR-027 年代一模一樣。"""
    ass = main.render_timings(_doc(font_size=100))
    assert _pos_y(ass, 0) == (1080 - 100) - round(100 * 1.45)
