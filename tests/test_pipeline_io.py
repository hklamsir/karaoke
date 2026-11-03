# -*- coding: utf-8 -*-
"""9.2 第 1 / 2 / 9 項 + §2.1 / §2.10 / §2.12 —— 管線 I/O 與外部工具邊界。

對應 docs/代碼審查報告.html：
- 9.2-1  空音訊 / 全靜音
- 9.2-2  損壞音訊 / 假 ffmpeg
- 9.2-9  輸出目錄不存在
- §2.1   P0 跨磁碟區 relpath 崩潰
- §2.10  hex 色碼未驗證字元集
- §2.12  read_playres 對怪值不崩

⚠️ ``xfail(strict=True)`` 契約：功能未實作時 xfail（suite 保持綠）；
實作完成後自動 XPASS → strict 判 FAILED，強制開發者回來拆標記。
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import main

# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------


def write_wav(path: Path, samples: list[int], rate: int = 16000) -> Path:
    """寫一個 16-bit mono PCM WAV。

    Args:
        path: 輸出路徑。
        samples: 取樣值（-32768..32767）。
        rate: 取樣率。

    Returns:
        輸出路徑。
    """
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(struct.pack("<%dh" % len(samples), *samples))
    return path


def sine_samples(freq_hz: float, seconds: float = 1.0, rate: int = 16000,
                 amplitude: int = 20000) -> list[int]:
    """產生正弦波取樣（``freq_hz=0`` 即全零 = 全靜音）。"""
    total = int(rate * seconds)
    if freq_hz == 0:
        return [0] * total
    return [int(amplitude * math.sin(2 * math.pi * freq_hz * i / rate)) for i in range(total)]


# ---------------------------------------------------------------------------
# 9.2-1 · 全靜音偵測（待實作 main.detect_silent_wav）
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="9.2-1 待實作 main.detect_silent_wav（stdlib wave 讀樣本）")
def test_detect_silent_wav_true_for_all_zero(tmp_path: Path) -> None:
    """全零 WAV → 判定為靜音（等價於報告的「peaks 全零」）。"""
    wav = write_wav(tmp_path / "silent.wav", sine_samples(0, 1.0))
    assert main.detect_silent_wav(wav) is True


@pytest.mark.xfail(strict=True, reason="9.2-1 待實作 main.detect_silent_wav（stdlib wave 讀樣本）")
def test_detect_silent_wav_false_for_tone(tmp_path: Path) -> None:
    """有聲正弦波 → 唔可以誤判為靜音（防「全靜音偵測」本身過度敏感）。"""
    wav = write_wav(tmp_path / "tone.wav", sine_samples(440, 1.0))
    assert main.detect_silent_wav(wav) is False


# ---------------------------------------------------------------------------
# 9.2-2 · 損壞音訊 / 假 ffmpeg（待實作 AudioExtractionError / ToolMissingError）
# ---------------------------------------------------------------------------


def test_extract_temp_audio_raises_on_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    """ffmpeg 回非零 → 拋 AudioExtractionError（唔再用 sys.exit 中斷）。"""
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"\x00\x00\x00\x18")      # 4 bytes 垃圾

    def fake_run(*args, **kwargs):              # noqa: ANN002, ANN003
        return SimpleNamespace(returncode=1, stderr="Invalid data found when processing input")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    with pytest.raises(main.AudioExtractionError):
        main.extract_temp_audio("ffmpeg", video, tmp_path)


def test_extract_temp_audio_raises_tool_missing_for_absent_binary(tmp_path: Path) -> None:
    """ffmpeg 路徑指向不存在的檔 → 應是語意化的 ToolMissingError，而非裸 FileNotFoundError。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"\x00" * 16)

    with pytest.raises(main.ToolMissingError):
        main.extract_temp_audio(str(tmp_path / "no_such_ffmpeg.exe"), video, tmp_path)


def test_extract_temp_audio_writes_16k_mono(tmp_path: Path) -> None:
    """正常路徑（有真 ffmpeg 才跑）：輸出必須係 16kHz / 單聲道 / 16-bit。"""
    pytest.importorskip("shutil")
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("本機無 ffmpeg，離線環境跳過整合測試")

    import subprocess

    src = tmp_path / "tone.wav"
    write_wav(src, sine_samples(440, 0.5))
    out = main.extract_temp_audio(ffmpeg, src, tmp_path)

    with wave.open(str(out), "rb") as fh:
        assert fh.getframerate() == 16000
        assert fh.getnchannels() == 1
        assert fh.getsampwidth() == 2
    assert subprocess.run([ffmpeg, "-version"], capture_output=True).returncode == 0


# ---------------------------------------------------------------------------
# 9.2-9 · 輸出目錄不存在 → 自動建立
# ---------------------------------------------------------------------------


def test_generate_creates_missing_output_dir(tmp_path: Path) -> None:
    """輸出目錄不存在時 generate_timings 應自行 mkdir（在失敗之前已完成）。

    唔斷言例外類型：實作前係 ``SystemExit``、實作後係 ``KaraokeError``，
    兩者都唔繼承 ``Exception``，所以統一用 ``BaseException`` 吞掉，
    只驗「目錄已建立」這個與重構無關的行為契約。
    """
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"\x00\x00\x00\x18")
    out_dir = tmp_path / "nested" / "deep"

    opts = main.GenerateOptions(input=video, output_dir=out_dir, api_key="sk-test-not-real")
    try:
        main.generate_timings(opts)
    except BaseException:                       # noqa: BLE001 — 見 docstring
        pass

    assert out_dir.is_dir(), "generate 應該在接觸 ffmpeg 之前就建立輸出目錄"


# ---------------------------------------------------------------------------
# §2.1 P0 · 跨磁碟區 relpath
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="§2.1 P0 待修：relpath 跨磁碟區拋 ValueError 未被捕捉")
def test_burn_cross_drive_relpath_does_not_crash(tmp_path: Path, monkeypatch) -> None:
    """模擬 Windows 跨磁碟區（relpath 拋 ValueError）→ 燒錄應該改用絕對路徑繼續。

    輸入／輸出是 ffmpeg 的**參數**（不是 filter 內容），絕對路徑本來就安全，
    需要相對化的只有 ``-vf ass=<檔名>``，而該處已用「cwd + 純檔名」處理。
    """
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    work = tmp_path / "work"
    work.mkdir()
    ass = work / "a.ass"
    ass.write_text("[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n", encoding="utf-8")
    out = work / "out.mp4"

    def boom(*args, **kwargs):                  # noqa: ANN002, ANN003
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(main.os.path, "relpath", boom)

    def fake_run(cmd, **kwargs):                # noqa: ANN001, ANN003
        # 模擬 ffmpeg 成功：在 cwd 下落實體輸出檔（cmd[-1] 可能是絕對或相對路徑）
        target = Path(cmd[-1])
        if not target.is_absolute():
            target = Path(kwargs.get("cwd", ".")) / target
        target.write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac")
    assert out.is_file()


def test_burn_uses_bare_filename_in_filter(tmp_path: Path, monkeypatch) -> None:
    """ADR-006 鐵律回歸鎖：``-vf ass=`` 內只准有檔名，唔可以出現路徑或冒號。"""
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake")
    work = tmp_path / "work"
    work.mkdir()
    ass = work / "C_奇怪的:名字.ass"
    ass.write_text("[Script Info]\n", encoding="utf-8")
    out = work / "out.mp4"

    seen: list[list[str]] = []

    def fake_run(cmd, **kwargs):                # noqa: ANN001, ANN003
        seen.append(list(cmd))
        (Path(kwargs["cwd"]) / cmd[-1]).write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac")

    vf = seen[0][seen[0].index("-vf") + 1]
    assert vf.startswith("ass=")
    assert ":" not in vf.split(":original_size")[0][len("ass="):], \
        "filter 內的 ass 檔名唔可以含冒號（會被 ffmpeg 當 option 分隔符）"
    assert vf.endswith(":original_size=1920x1080")


# ---------------------------------------------------------------------------
# §2.10 · hex 色碼驗證
# ---------------------------------------------------------------------------


def test_hex_color_converts_to_bgr() -> None:
    """正常值：``#FFFF00``（黃）→ ``&H0000FFFF``（ASS 為 BGR 反序）。"""
    assert main.hex_to_ass_color("#FFFF00") == "&H0000FFFF"
    assert main.hex_to_ass_color("000000") == "&H00000000"


@pytest.mark.xfail(strict=True, reason="§2.10 待修：hex_to_ass_color 只檢查長度，未驗證字元集")
def test_hex_color_rejects_non_hex_characters() -> None:
    """``#ZZZZZZ`` 長度合法但字元非法 → 應該拋 ValueError，而非靜默輸出非法色碼。"""
    with pytest.raises(ValueError):
        main.hex_to_ass_color("#ZZZZZZ")


def test_hex_color_rejects_wrong_length() -> None:
    """長度不對已有防護（現行行為，回歸鎖）。"""
    with pytest.raises(ValueError):
        main.hex_to_ass_color("#FFF")


# ---------------------------------------------------------------------------
# §2.12 · read_playres_from_ass
# ---------------------------------------------------------------------------


def test_read_playres_parses_valid_ass(tmp_path: Path) -> None:
    ass = tmp_path / "a.ass"
    ass.write_text(
        "[Script Info]\nTitle: x\nPlayResX: 1280\nPlayResY: 720\n\n[Events]\n",
        encoding="utf-8",
    )
    assert main.read_playres_from_ass(ass) == (1280, 720)


def test_read_playres_returns_none_when_missing(tmp_path: Path) -> None:
    """冇 PlayRes 字段 → None（burn 會 fallback，唔應該拋）。"""
    ass = tmp_path / "a.ass"
    ass.write_text("[Script Info]\nTitle: x\n", encoding="utf-8")
    assert main.read_playres_from_ass(ass) is None


def test_read_playres_tolerates_garbage(tmp_path: Path) -> None:
    """``PlayResX: abc`` → 解析不出數字，回 None 而**唔崩**（§2.12 底線）。"""
    ass = tmp_path / "a.ass"
    ass.write_text("[Script Info]\nPlayResX: abc\nPlayResY: def\n", encoding="utf-8")
    assert main.read_playres_from_ass(ass) is None
