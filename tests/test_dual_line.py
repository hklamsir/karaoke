# -*- coding: utf-8 -*-
"""ADR-027 雙行歌詞契約測試。

鎖住三件事：
1. 每句仍然只出一個 Dialogue（唔會因為雙行而倍增）
2. 顯示窗口符合「下一句開唱 + delay」的規則
3. 上行／下行的 \\an2\\pos 交替，且單行模式完全唔受影響
"""
from __future__ import annotations

import pytest

import main


def _make_doc(n: int = 4, *, dual: bool = True, delay: int = 1000,
              lead_in: int = 0) -> dict:
    """每句 3000ms、句句緊接（start_{i+1} = end_i + 200）。

    ``lead_in=0`` 明確關閉 ADR-031 的提前顯示 —— 本檔鎖的是 **ADR-027 舊契約**，
    要維持 byte-identical（提前顯示／圓點見 ``test_lead_in.py``）。
    """
    lines = []
    for i in range(n):
        start = i * 3000
        end = start + 2800
        lines.append({
            "start_ms": start,
            "end_ms": end,
            "text": f"第{i}句",
            "break_reason": "punctuation",
            "words": [
                {"text": "第", "punctuation": "", "estimated": False,
                 "chars": [{"text": "第", "begin_ms": start, "end_ms": start + 900}]},
                {"text": "句", "punctuation": "", "estimated": False,
                 "chars": [{"text": "句", "begin_ms": start + 900, "end_ms": end}]},
            ],
        })
    style = {
        "play_res_x": 1920, "play_res_y": 1080,
        "font_size": 100, "margin_v": 100, "outline_width": 4.0,
        "switch_delay_ms": delay, "lead_in_ms": lead_in,
    }
    if dual:
        style["dual_line"] = True
    return {"schema_version": 3, "source": {}, "style": style, "lines": lines}


def _events(ass: str) -> list[str]:
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]


def _times(event: str) -> tuple[str, str]:
    parts = event.split(",", 10)
    return parts[1], parts[2]


def test_dual_emits_one_event_per_line() -> None:
    """雙行唔會把每句拆成兩個 event（event 數 = 行數）。"""
    ass = main.render_timings(_make_doc(6))
    assert len(_events(ass)) == 6


def test_dual_display_windows() -> None:
    """第 i 句由「前一句開唱 + delay」出現，到「下一句開唱 + delay」消失。"""
    ass = main.render_timings(_make_doc(4, delay=1000))
    got = [_times(e) for e in _events(ass)]
    assert got == [
        ("0:00:00.00", "0:00:04.00"),   # L0: 0 → L1(3.0) + 1.0
        ("0:00:00.00", "0:00:07.00"),   # L1: 0 → L2(6.0) + 1.0
        ("0:00:04.00", "0:00:10.00"),   # L2: L1(3.0)+1.0 → L3(9.0)+1.0
        ("0:00:07.00", "0:00:11.80"),   # L3: L2(6.0)+1.0 → end_ms（最後一句）
    ]


def test_dual_rows_alternate() -> None:
    """奇數索引固定上行、偶數索引固定下行；上行 = 下行 - line_gap。"""
    ass = main.render_timings(_make_doc(4))
    events = _events(ass)
    down_y = 1080 - 100                      # margin_v=100
    up_y = down_y - round(100 * 1.45)        # font_size 100 → line_gap 145
    assert f"\\pos(960,{up_y})" in events[0]
    assert f"\\pos(960,{down_y})" in events[1]
    assert f"\\pos(960,{up_y})" in events[2]
    assert f"\\pos(960,{down_y})" in events[3]


def test_dual_leading_pause_keeps_karaoke_in_sync() -> None:
    """早出現的 event 要用前置 \\k 佔住時間軸，否則高亮會提早啟動。"""
    ass = main.render_timings(_make_doc(4, delay=1000))
    l2 = _events(ass)[2]                     # L2 於 4.0s 出現，但 6.0s 才開唱
    assert "{\\k200}" in l2                  # 2 秒 = 200 厘秒


def test_single_line_is_untouched() -> None:
    """冇 dual_line 時輸出同舊版一致：冇 \\pos、時間窗 = 句子本身。"""
    ass = main.render_timings(_make_doc(4, dual=False))
    events = _events(ass)
    assert all("\\pos(" not in e for e in events)
    assert _times(events[0]) == ("0:00:00.00", "0:00:02.80")


def test_dual_roundtrip_style() -> None:
    """dual_line / switch_delay_ms 要寫入 json 並原樣讀返。"""
    doc = _make_doc(3)
    lines, style = main.timings_doc_to_lines(doc)
    saved = main.lines_to_timings_doc(lines, style, doc["source"])
    assert saved["style"]["dual_line"] is True
    assert saved["style"]["switch_delay_ms"] == 1000

    again, style2 = main.timings_doc_to_lines(saved)
    assert style2.dual_line is True
    assert style2.switch_delay_ms == 1000


def test_short_gap_never_overlaps_same_row() -> None:
    """句距短過 delay 時，同一行的兩個 event 唔可以重疊。"""
    doc = _make_doc(4, delay=2000)
    ass = main.render_timings(doc)
    events = _events(ass)
    # 上行 = L0, L2；下行 = L1, L3
    up = [_times(events[i]) for i in (0, 2)]
    down = [_times(events[i]) for i in (1, 3)]
    for row in (up, down):
        for (_s0, e0), (s1, _e1) in zip(row, row[1:]):
            assert e0 <= s1


def test_shift_ms_moves_everything() -> None:
    """ADR-029：整體平移要同時移動 start 同 end。"""
    doc = _make_doc(3, dual=False)
    doc["style"]["shift_ms"] = 500
    events = _events(main.render_timings(doc))
    assert _times(events[0]) == ("0:00:00.50", "0:00:03.30")


def test_shift_ms_clamps_at_zero() -> None:
    """負平移唔可以產生負時間。"""
    doc = _make_doc(3, dual=False)
    doc["style"]["shift_ms"] = -1000
    events = _events(main.render_timings(doc))
    assert _times(events[0]) == ("0:00:00.00", "0:00:01.80")


def test_shift_ms_roundtrip() -> None:
    """shift_ms 非 0 才寫入 json，讀返要一致。"""
    doc = _make_doc(2, dual=False)
    doc["style"]["shift_ms"] = -250
    lines, style = main.timings_doc_to_lines(doc)
    saved = main.lines_to_timings_doc(lines, style, doc["source"])
    assert saved["style"]["shift_ms"] == -250
    assert main.timings_doc_to_lines(saved)[1].shift_ms == -250

    doc["style"].pop("shift_ms")
    lines2, style2 = main.timings_doc_to_lines(doc)
    assert "shift_ms" not in main.lines_to_timings_doc(lines2, style2, {})["style"]


def test_dual_respects_top_alignment() -> None:
    """ADR-030：對齊選「頂部置中」時，\an8 由畫面頂部往下排，唔再貼底。"""
    doc = _make_doc(2)
    doc["style"]["alignment"] = 8
    doc["style"]["margin_v"] = 80
    events = _events(main.render_timings(doc))
    assert "\\an8\\pos(960,80)" in events[0]      # 上行：頂部留白 80
    assert "\\an8\\pos(960,225)" in events[1]     # 下行：80 + line_gap 145


def test_dual_center_alignment() -> None:
    """中部置中：兩行圍繞畫面中心對稱。"""
    doc = _make_doc(2)
    doc["style"]["alignment"] = 5
    events = _events(main.render_timings(doc))
    assert "\\an5\\pos(960,468)" in events[0]     # 540 - 145/2
    assert "\\an5\\pos(960,612)" in events[1]     # 540 + 145//2


def test_dual_horizontal_offsets() -> None:
    """ADR-030：上行偏左、下行偏右（% 畫面闊度）。"""
    doc = _make_doc(2)
    doc["style"]["up_offset_pct"] = -10
    doc["style"]["down_offset_pct"] = 10
    events = _events(main.render_timings(doc))
    assert "\\pos(768," in events[0]              # 960 - 192
    assert "\\pos(1152," in events[1]             # 960 + 192


def test_offsets_roundtrip() -> None:
    """水平錯位要寫入 json 並讀返。"""
    doc = _make_doc(2)
    doc["style"]["up_offset_pct"] = -10
    doc["style"]["down_offset_pct"] = 10
    lines, style = main.timings_doc_to_lines(doc)
    saved = main.lines_to_timings_doc(lines, style, doc["source"])
    assert saved["style"]["up_offset_pct"] == -10
    assert saved["style"]["down_offset_pct"] == 10
