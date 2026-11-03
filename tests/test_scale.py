# -*- coding: utf-8 -*-
"""9.2 第 6 / 7 項 + §2.8 —— 規模與原子寫入。

對應 docs/代碼審查報告.html：
- 9.2-6  超長音訊（3 小時 timings doc 也要在 2 秒內渲染完）
- 9.2-7  超大歌詞檔（5,000 行；記憶體要平坦，不能 O(n²)）
- §2.8   generate / render 非原子寫出（UI 已有 atomic_write_json，CLI 未有）

標記 ``slow``：預設會跑，可用 ``-m "not slow"`` 跳過做快速回歸。
"""

from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path

import pytest

import main
from ui import models

from fixtures.builder import build_doc, build_long_audio_doc

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# 9.2-6 · 超長音訊
# ---------------------------------------------------------------------------


def test_long_audio_doc_renders_within_budget() -> None:
    """3 小時 timings doc：parse + render 合計 < 2 秒（回歸防護，非效能微調）。"""
    # 先暖機（import / 首次 f-string 編譯成本唔應該計入）
    small = build_doc(10)
    lines, style = main.timings_doc_to_lines(small)
    main.build_karaoke_ass(lines, style)

    doc = build_long_audio_doc(3 * 60 * 60 * 1000)
    assert len(doc["lines"]) > 2000, "3 小時應該產生 >2000 行，否則夾具唔夠長"

    started = time.perf_counter()
    lines, style = main.timings_doc_to_lines(doc)
    ass = main.build_karaoke_ass(lines, style)
    elapsed = time.perf_counter() - started

    assert ass.count("Dialogue:") == len(doc["lines"])
    assert elapsed < 2.0, f"3 小時 doc 渲染用了 {elapsed:.2f}s，超出 2s 預算"


def test_long_audio_doc_times_are_monotonic() -> None:
    """超長 doc 也不可以出現負 duration（整數毫秒運算，不容浮點漂移）。"""
    doc = build_long_audio_doc(60 * 60 * 1000)
    lines, _ = main.timings_doc_to_lines(doc)
    for a, b in zip(lines, lines[1:]):
        assert a.end_ms <= b.start_ms
    assert all(line.end_ms > line.start_ms for line in lines)


# ---------------------------------------------------------------------------
# 9.2-7 · 超大歌詞檔
# ---------------------------------------------------------------------------


def test_huge_doc_normalize_and_write_is_flat(tmp_path: Path) -> None:
    """5,000 行 doc：normalize + 原子寫入，峰值記憶體 < 300MB（寬鬆上限，只抓 O(n²)）。"""
    doc = build_doc(5000, chars_per_line=8, words_per_line=2)
    assert len(doc["lines"]) == 5000

    out = tmp_path / "big_timings.json"
    tracemalloc.start()
    try:
        models.normalize(doc)
        models.atomic_write_json(out, doc)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert out.is_file()
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert len(loaded["lines"]) == 5000
    assert peak < 300 * 1024 * 1024, f"峰值記憶體 {peak / 1_048_576:.0f}MB 超出 300MB"


def test_huge_doc_renders_all_lines(tmp_path: Path) -> None:
    """5,000 行全部要出 Dialogue（唔可以中途截斷）。"""
    doc = build_doc(5000)
    lines, style = main.timings_doc_to_lines(doc)
    ass = main.build_karaoke_ass(lines, style)
    assert ass.count("Dialogue:") == 5000


# ---------------------------------------------------------------------------
# §2.8 · 原子寫入
# ---------------------------------------------------------------------------


def test_atomic_write_keeps_original_when_replace_fails(tmp_path: Path, monkeypatch) -> None:
    """``os.replace`` 失敗 → 舊檔內容必須完整（唔可以留半截 JSON）。"""
    target = tmp_path / "t.json"
    target.write_text('{"old": true}', encoding="utf-8")

    def boom(*args, **kwargs):                  # noqa: ANN002, ANN003
        raise OSError("模擬斷電 / 磁碟滿")

    monkeypatch.setattr(models.os, "replace", boom)

    with pytest.raises(OSError):
        models.atomic_write_json(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}


def test_atomic_write_creates_bak(tmp_path: Path) -> None:
    """覆寫前留 ``.bak``（E2 / R6 對策）。"""
    target = tmp_path / "t.json"
    models.atomic_write_json(target, {"v": 1})
    models.atomic_write_json(target, {"v": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 2}
    bak = target.with_suffix(target.suffix + ".bak")
    assert bak.is_file()
    assert json.loads(bak.read_text(encoding="utf-8")) == {"v": 1}


@pytest.mark.xfail(strict=True, reason="§2.8 待實作：main.atomic_write_json（CLI 目前直接 write_text 覆寫）")
def test_main_exposes_atomic_write_json(tmp_path: Path) -> None:
    """CLI 側亦應有同一個原子寫入 helper（PR-1/PR-2 把它搬到共用層）。"""
    target = tmp_path / "t.json"
    main.atomic_write_json(target, {"v": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}
