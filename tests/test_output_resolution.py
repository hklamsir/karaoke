# -*- coding: utf-8 -*-
"""ADR-051 成品解析度契約測試。

用戶回報：「原本 1080×1080，改解析度為 1920×1080，燒錄後還是 1080×1080」。
根因是燒錄 filter chain 只有 ``ass=``，完全沒有 scale —— 成品尺寸永遠等於原片；
預覽的「解析度」下拉只改 PlayRes（字幕基準），而且每次開檔都會被
``_align_play_res()`` 抹走。

本檔鎖住三件事：
1. ``_burn_base(out_size=...)`` 會加 ``scale`` + ``pad``，而 ``ass=`` 仍然係**最後**一個 filter
2. ``out_size=None`` 時命令與舊版**逐字相同**（舊專案行為不變）
3. 燒錄對話框的「成品解析度」說明文字（純函數，唔開窗）
"""
from __future__ import annotations

from pathlib import Path

import pytest

import main
from ui.widgets.burn_dialog import output_res_text


def _base(tmp_path: Path, video: Path, *, out_size=None) -> list[str]:
    return main._burn_base("ffmpeg", video, "x.ass", 640, 360, None, tmp_path, 0,
                           out_size=out_size)


def _vf(cmd: list[str]) -> str:
    return cmd[cmd.index("-vf") + 1]


def test_out_size_adds_scale_and_pad_before_ass(tmp_path: Path) -> None:
    """指定成品尺寸 → 先縮放補邊，最後才 ass。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"x")

    vf = _vf(_base(tmp_path, video, out_size=(1920, 1080)))

    assert vf.startswith("scale=1920:1080:force_original_aspect_ratio=decrease"), vf
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2" in vf
    assert vf.endswith("ass=x.ass:original_size=640x360"), "ass 必須係最後一個 filter"


def test_out_size_keeps_aspect_with_black_bars(tmp_path: Path) -> None:
    """方形成品（1080×1080 專案）：用 contain 補邊，唔可以改成 crop 裁畫面。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"x")

    vf = _vf(_base(tmp_path, video, out_size=(1080, 1080)))

    assert "crop=" not in vf, "補邊方案唔可以裁走畫面"
    assert "pad=1080:1080" in vf
    assert "color=0x000000" in vf


def test_no_out_size_is_byte_identical_to_old_behaviour(tmp_path: Path) -> None:
    """``out_size=None`` = 舊行為（唔加任何 scale/pad）。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"x")

    assert _vf(_base(tmp_path, video)) == "ass=x.ass:original_size=640x360"


# ---------------------------------------------------------------------------
# 燒錄對話框文案
# ---------------------------------------------------------------------------

def test_output_res_text_follows_source_when_unset() -> None:
    assert output_res_text(None, (1080, 1080)) == "成品解析度：跟燒錄來源（1080×1080）"


def test_output_res_text_reports_resize_and_padding() -> None:
    """1080×1080 → 1920×1080：比例唔同，要講明補黑邊。"""
    text = output_res_text((1920, 1080), (1080, 1080))

    assert "1920×1080" in text and "1080×1080" in text
    assert "補黑邊" in text


def test_output_res_text_reports_plain_scale_for_same_aspect() -> None:
    text = output_res_text((1920, 1080), (1280, 720))

    assert "等比例縮放" in text
    assert "補黑邊" not in text


def test_output_res_text_matches_source() -> None:
    assert output_res_text((1920, 1080), (1920, 1080)) == \
        "成品解析度：1920×1080（與來源相同）"

# ---------------------------------------------------------------------------
# CLI --out-size 解析
# ---------------------------------------------------------------------------

def test_parse_size_accepts_common_separators() -> None:
    assert main.parse_size("1920x1080") == (1920, 1080)
    assert main.parse_size(" 1280×720 ") == (1280, 720)
    assert main.parse_size("640*480") == (640, 480)


def test_parse_size_empty_means_follow_source() -> None:
    assert main.parse_size(None) is None
    assert main.parse_size("   ") is None


def test_parse_size_rejects_garbage() -> None:
    with pytest.raises(main.KaraokeError):
        main.parse_size("big")
    with pytest.raises(main.KaraokeError):
        main.parse_size("1x1")      # 太細（< 16px）唔合理
