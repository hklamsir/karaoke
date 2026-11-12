# -*- coding: utf-8 -*-
"""燒錄音源比對（ADR-020）—— ``compare_sources()`` 純邏輯驗證。

來源：``_probe/probe_burn_source.py`` §1（原為人手 probe，每次靠肉眼睇輸出）。
這些斷言本來就有價值，只是一直沒有自動跑 —— 現在搬入 pytest。
需要 ffmpeg 生成素材 + ffprobe 探測，但**唔需要 Tk**（唔開任何視窗）。

素材全部由 ``tests/fixtures/media.py`` 即場合成，零第三方版權內容。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main

# burn_dialog 在 module level import ttkbootstrap，CI 預設冇裝 → 優雅 skip
pytest.importorskip("ttkbootstrap", reason="ui.widgets.burn_dialog 需要 ttkbootstrap")

from ui.widgets import burn_dialog  # noqa: E402


# ---------------------------------------------------------------------------
# compare_sources 四種情境
# ---------------------------------------------------------------------------


def test_same_length_same_aspect_has_no_warning(media: dict[str, Path], ffprobe: str) -> None:
    """同長度、同比例 → 零警告、可用。"""
    warnings, usable = burn_dialog.compare_sources(ffprobe, media["same"], media["orig"])
    assert usable is True
    assert warnings == []


def test_audio_only_file_is_usable_with_warning(media: dict[str, Path], ffprobe: str) -> None:
    """揀到純音檔（無視訊軌）→ ADR-039 起可用，但必須有「會用背景合成」警告。"""
    warnings, usable = burn_dialog.compare_sources(ffprobe, media["wav"], media["orig"])
    assert usable is True
    assert warnings, "音檔可燒就更加要講清楚會用背景合成"
    assert any("冇視訊軌" in w for w in warnings), f"實得：{warnings}"
    assert any("背景" in w for w in warnings), f"要講明會用背景合成，實得：{warnings}"


def test_short_clip_warns_but_stays_usable(media: dict[str, Path], ffprobe: str) -> None:
    """短 3 秒 → 出時長警告（門檻 1500ms），但仍然可燒（用戶可能刻意用短片）。"""
    warnings, usable = burn_dialog.compare_sources(ffprobe, media["short"], media["orig"])
    assert usable is True
    assert any("相差" in w for w in warnings), f"應該有時間差警告，實得：{warnings}"


def test_different_aspect_ratio_warns_but_stays_usable(media: dict[str, Path], ffprobe: str) -> None:
    """16:9 → 1:1 → 出比例警告（字幕位置可能要重調），仍然可燒。"""
    warnings, usable = burn_dialog.compare_sources(ffprobe, media["square"], media["orig"])
    assert usable is True
    assert any("比例" in w for w in warnings), f"應該有比例警告，實得：{warnings}"


# ---------------------------------------------------------------------------
# 邊界：ffprobe 為空 = 跳過比對（唔阻塞燒錄）
# ---------------------------------------------------------------------------


def test_empty_ffprobe_skips_comparison(media: dict[str, Path]) -> None:
    """``ffprobe=""`` 代表環境無 ffprobe → 直接放行，唔可以當成「不可用」。"""
    warnings, usable = burn_dialog.compare_sources("", media["wav"], media["orig"])
    assert (warnings, usable) == ([], True)


def test_compare_does_not_require_original_to_exist(media: dict[str, Path], ffprobe: str) -> None:
    """原片路徑失效時唔應該拋 —— 最壞情況是「無從比對」，不是崩潰。"""
    warnings, usable = burn_dialog.compare_sources(ffprobe, media["same"], media["orig"].with_suffix(".gone.mp4"))
    assert usable is True
    assert isinstance(warnings, list)
