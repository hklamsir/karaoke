# -*- coding: utf-8 -*-
"""預覽畫面比例契約測試（ADR-042）。

用戶回報：4:3 的 MV 在 1920×1080 預覽下被拉闊、字幕睇落「冇縮小」。
根因是畫面框用「解析度」的比例繪製，而字幕用 timings 的 PlayRes 縮放，
兩者座標系不同。`fit_frame()` 鎖住「比例永遠跟原片」這個契約。
"""
from __future__ import annotations

import pytest

from ui.widgets.preview import fit_frame


def test_43_source_keeps_43_in_169_window() -> None:
    """4:3 原片在 16:9 窗格 → 畫面仍然 4:3（唔會被拉闊）。"""
    fw, fh, _scale = fit_frame(1000, 500, 640, 480, 1920, 1080, None)
    assert fw / fh == pytest.approx(640 / 480)
    assert fh <= 500 + 1e-6
    assert fw <= 1000 + 1e-6


def test_169_source_fills_169_window() -> None:
    """16:9 原片在 16:9 窗格 → 貼邊填滿。"""
    fw, fh, _ = fit_frame(1000, 562.5, 1920, 1080, 1920, 1080, None)
    assert fw == pytest.approx(1000)
    assert fh == pytest.approx(562.5)


def test_smaller_output_res_caps_scale() -> None:
    """輸出模擬 320×240（比原片細）→ 唔可以放大超過 0.5 倍。"""
    _fw, _fh, scale = fit_frame(1000, 500, 640, 480, 320, 240, None)
    assert scale == pytest.approx(0.5)


def test_larger_output_res_does_not_limit() -> None:
    """輸出模擬 4K → 唔應該封頂，交由窗格決定。"""
    _fw, _fh, scale = fit_frame(1000, 500, 640, 480, 3840, 2160, None)
    assert scale == pytest.approx(min(1000 / 640, 500 / 480))


def test_zoom_100pct_degrades_when_window_too_small() -> None:
    """選 100% 但窗格放唔落 → 自動降級成「適合視窗」，唔會爆框。"""
    fw, fh, scale = fit_frame(300, 200, 640, 480, 640, 480, 1.0)
    assert fw <= 300 + 1e-6 and fh <= 200 + 1e-6
    assert scale == pytest.approx(min(300 / 640, 200 / 480))


def test_zoom_50pct_applied_when_it_fits() -> None:
    """選 50% 且窗格夠大 → 就係 0.5。"""
    _fw, _fh, scale = fit_frame(1000, 800, 640, 480, 1920, 1080, 0.5)
    assert scale == pytest.approx(0.5)


def test_zero_window_does_not_crash() -> None:
    """窗格未 layout（0×0）→ 仍然回正數，唔會 ZeroDivisionError。"""
    fw, fh, scale = fit_frame(0, 0, 640, 480, 1920, 1080, None)
    assert fw > 0 and fh > 0 and scale > 0


def test_aspect_is_independent_of_output_res() -> None:
    """同一個原片，無論輸出模擬揀 720p 定 1080p，畫面闊高比都要一樣。"""
    a = fit_frame(1000, 500, 854, 480, 1280, 720, None)
    b = fit_frame(1000, 500, 854, 480, 1920, 1080, None)
    assert a[0] / a[1] == pytest.approx(b[0] / b[1])
