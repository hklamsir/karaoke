# -*- coding: utf-8 -*-
"""燒錄端到端整合測試（真實 ffmpeg）—— 9.2 第 2 項的「實燒」配套。

來源：``_probe/probe_burn_source.py`` §2（原為人手 probe，只驗「有冇出檔」）。
本檔把同一件事自動化，並補上一條 probe 冇的斷言：**字幕真係燒了上去**
（比對同一時刻的像素差異），否則「輸出存在但冇字幕」這個經典失敗會漏網。

標記 ``slow``：整段約 3–5 秒（4 段素材生成 + 一次 libx264 crf18 編碼）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import main

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def _require_ffmpeg_on_path() -> str:
    """``main.burn_video`` 內部用 ``find_ffmpeg()``（只認 PATH），先確認可用。"""
    found = shutil.which("ffmpeg")
    if not found:
        pytest.skip("main.find_ffmpeg() 只認 PATH；請把 ffmpeg 加入 PATH 才跑本測試")
    return found


def _frame_rgb(ffmpeg: str, path: Path, at_seconds: float) -> bytes:
    """抽一格並解成 raw RGB24（避免依賴 PIL / numpy）。"""
    proc = subprocess.run(
        [ffmpeg, "-v", "error", "-ss", str(at_seconds), "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"抽格失敗：{path} @ {at_seconds}s")
    return proc.stdout


def _strong_diff_pixels(a: bytes, b: bytes, threshold: int = 60) -> int:
    """計算「任一通道差異 > threshold」的像素數（避開重新編碼的細微雜訊）。"""
    count = 0
    for i in range(0, min(len(a), len(b)), 3):
        if (abs(a[i] - b[i]) > threshold
                or abs(a[i + 1] - b[i + 1]) > threshold
                or abs(a[i + 2] - b[i + 2]) > threshold):
            count += 1
    return count


def test_burn_output_is_playable_and_keeps_audio(media: dict[str, Path],
                                                 ffprobe: str, tmp_path: Path) -> None:
    """實燒一段 10 秒片：輸出存在、時長跟原片、視訊/音軌齊全、解像度不變。"""
    _require_ffmpeg_on_path()
    out = tmp_path / "burned.mp4"

    main.burn_video(media["same"], media["ass"], out)

    assert out.is_file() and out.stat().st_size > 100_000, "輸出檔太細，可能係空燒"
    duration = main.probe_duration_ms(ffprobe, out) or 0
    assert 9_500 <= duration <= 10_600, f"輸出時長 {duration}ms 偏離原片 10s"
    assert main.probe_has_video(ffprobe, out) is True
    assert main.probe_audio_codec(ffprobe, out) is not None, "音軌唔見了（ADR-009 鐵律）"
    assert main.probe_video_size(ffprobe, out) == (640, 360)


def test_subtitles_are_actually_burned(media: dict[str, Path], tmp_path: Path) -> None:
    """同一時刻，輸出畫面必須與原片**明顯不同** —— 否則等於冇燒字幕。

    用「強差異像素數」而非「有任何差異」：crf18 重新編碼本身就會造成細微差異，
    只有字幕（黃字 + 黑邊）才會產生大量高對比像素。
    """
    ffmpeg = _require_ffmpeg_on_path()
    out = tmp_path / "burned_sub.mp4"

    main.burn_video(media["same"], media["ass"], out)

    # ASS 的字幕時段為 0.20s–8.80s，取 1.0s（字幕已完整顯示）
    before = _frame_rgb(ffmpeg, media["same"], 1.0)
    after = _frame_rgb(ffmpeg, out, 1.0)
    assert len(before) == len(after), "兩格解像度不同，無法比較"

    changed = _strong_diff_pixels(before, after)
    total = len(before) // 3
    assert changed > total * 0.005, (
        f"只有 {changed}/{total} 像素有強差異（<0.5%），字幕可能冇燒上去"
    )


def test_burn_outside_subtitle_window_is_near_identical(media: dict[str, Path],
                                                        tmp_path: Path) -> None:
    """字幕時段以外（9.5s > 8.8s）→ 差異應該遠少於字幕時段。

    這條是上一條的反向對照：證明「有差異」確實來自字幕，而非整段重編碼。
    """
    ffmpeg = _require_ffmpeg_on_path()
    out = tmp_path / "burned_window.mp4"

    main.burn_video(media["same"], media["ass"], out)

    with_sub = _strong_diff_pixels(_frame_rgb(ffmpeg, media["same"], 1.0),
                                   _frame_rgb(ffmpeg, out, 1.0))
    without_sub = _strong_diff_pixels(_frame_rgb(ffmpeg, media["same"], 9.5),
                                      _frame_rgb(ffmpeg, out, 9.5))
    assert with_sub > without_sub * 3, (
        f"字幕時段差異 {with_sub} 唔顯著大過非字幕時段 {without_sub}，"
        "可能畫面本身變化就蓋過字幕效果"
    )
