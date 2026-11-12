# -*- coding: utf-8 -*-
"""ADR-039 音檔燒錄背景合成契約測試。

只驗命令結構（唔跑 ffmpeg）—— 真正燒錄由 ``_probe`` 手動驗證。
鎖住三個易錯點：
1. ``ass=`` 一定要在 filter chain **最後**（否則字被 scale/crop 波及）
2. 圖片／純色來源要用 ``-map`` 明確指定，唔靠 ffmpeg 猜
3. 無限長來源（-loop 1 / lavfi color）一定要有 ``-t`` 或 ``-shortest`` 封頂
"""
from __future__ import annotations

from pathlib import Path

import pytest

import main


def _base(tmp_path: Path, video: Path, background: dict | None,
          total_ms: int = 1000) -> list[str]:
    return main._burn_base("ffmpeg", video, "x.ass", 1920, 1080,
                           background, tmp_path, total_ms)


def _vf(cmd: list[str]) -> str:
    return cmd[cmd.index("-vf") + 1]


def _arg(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# ---------------------------------------------------------------------------
# 有視訊軌：行為必須同以前一模一樣
# ---------------------------------------------------------------------------

def test_plain_video_command_unchanged(tmp_path: Path) -> None:
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"x")
    cmd = _base(tmp_path, video, None)

    assert _vf(cmd) == "ass=x.ass:original_size=1920x1080"
    assert "-map" not in cmd, "有視訊軌時唔應該加 -map（會改變預設串流選擇）"
    assert "-t" not in cmd and "-shortest" not in cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264"


# ---------------------------------------------------------------------------
# 純音檔 + 純色
# ---------------------------------------------------------------------------

def test_color_background_uses_lavfi(tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    cmd = _base(tmp_path, song, {"mode": "color", "color": "#102030"})

    assert "color=c=0x102030:s=1920x1080:r=25" in cmd
    assert "-f" in cmd and _arg(cmd, "-f") == "lavfi"
    assert _vf(cmd) == "ass=x.ass:original_size=1920x1080", "ass 必須最後一個 filter"
    assert _arg(cmd, "-map") == "0:v:0"
    assert cmd[-2] == "-t" and cmd[-1] == "1.000"


def test_color_background_without_duration_uses_shortest(tmp_path: Path) -> None:
    """讀唔到時長 → 用 -shortest 封頂，否則 lavfi 畫面永遠唔停。"""
    song = tmp_path / "song.wav"
    song.write_bytes(b"x")
    cmd = _base(tmp_path, song, {"mode": "color", "color": "#000000"}, total_ms=0)
    assert "-shortest" in cmd
    assert "-t" not in cmd


# ---------------------------------------------------------------------------
# 純音檔 + 圖片
# ---------------------------------------------------------------------------

def test_image_background_cover_scale_then_ass(tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"x")
    cmd = _base(tmp_path, song, {"mode": "image", "image": cover, "fit": "cover"})

    vf = _vf(cmd)
    assert vf.startswith(
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,")
    assert vf.endswith("ass=x.ass:original_size=1920x1080")
    assert "-loop" in cmd and _arg(cmd, "-loop") == "1"
    assert _arg(cmd, "-map") == "0:v:0"


def test_image_background_contain_pads_with_color(tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"x")
    cmd = _base(tmp_path, song, {"mode": "image", "image": cover,
                                 "fit": "contain", "color": "#112233"})
    vf = _vf(cmd)
    assert "force_original_aspect_ratio=decrease" in vf
    assert "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x112233" in vf
    assert "crop=" not in vf


def test_image_mode_without_file_falls_back_to_color(tmp_path: Path) -> None:
    """揀了「圖片」但未揀檔 → 退回純色，唔應該拋錯（UI 會 disable 開始鍵）。"""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    cmd = _base(tmp_path, song, {"mode": "image", "image": None})
    assert "color=c=0x000000:s=1920x1080:r=25" in cmd


def test_missing_image_raises_burn_error(tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    with pytest.raises(main.BurnError, match="背景圖片唔存在"):
        _base(tmp_path, song, {"mode": "image",
                               "image": tmp_path / "nope.jpg"})


def test_relative_paths_stay_relative(tmp_path: Path) -> None:
    """ADR-006：workdir 內的檔案用相對路徑，避免 Windows 的 ``C:`` 被當 filter 分隔符。"""
    song = tmp_path / "song.mp3"
    song.write_bytes(b"x")
    cmd = _base(tmp_path, song, {"mode": "color", "color": "#000000"})
    assert "song.mp3" in cmd
    assert not any(Path(part).is_absolute() for part in cmd
                   if part.endswith((".mp3", ".ass", ".jpg")))
