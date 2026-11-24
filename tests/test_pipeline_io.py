# -*- coding: utf-8 -*-
"""9.2 第 1 / 2 / 9 項 + §2.1 / §2.10 / §2.12 —— 管線 I/O 與外部工具邊界。

對應 docs/代碼審查報告.html：
- 9.2-1  空音訊 / 全靜音
- 9.2-2  損壞音訊 / 假 ffmpeg
- 9.2-9  輸出目錄不存在
- §2.1   P0 跨磁碟區 relpath 崩潰
- §2.10  hex 色碼未驗證字元集
- §2.12  read_playres 對怪值不崩

這些測試原本是 ``xfail(strict=True)`` 契約（未實作時 xfail、實作完成後
XPASS 會令 suite 變紅逼人拆標記）。9.2-1／9.2-2／§2.1 已於 ADR-025 實作，
標記已拆 —— 現在是普通回歸測試。
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
# 9.2-1 · 全靜音偵測（main.detect_silent_wav，ADR-025）
# ---------------------------------------------------------------------------


def test_detect_silent_wav_true_for_all_zero(tmp_path: Path) -> None:
    """全零 WAV → 判定為靜音（等價於報告的「peaks 全零」）。"""
    wav = write_wav(tmp_path / "silent.wav", sine_samples(0, 1.0))
    assert main.detect_silent_wav(wav) is True


def test_detect_silent_wav_false_for_tone(tmp_path: Path) -> None:
    """有聲正弦波 → 唔可以誤判為靜音（防「全靜音偵測」本身過度敏感）。"""
    wav = write_wav(tmp_path / "tone.wav", sine_samples(440, 1.0))
    assert main.detect_silent_wav(wav) is False


# ---------------------------------------------------------------------------
# 9.2-2 · 損壞音訊 / 假 ffmpeg（AudioExtractionError / ToolMissingError）
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


def test_hex_color_accepts_case_and_missing_hash() -> None:
    """大小寫不拘、``#`` 可省略 —— 驗證唔可以誤殺合法輸入。"""
    assert main.hex_to_ass_color("#abcdef") == "&H00EFCDAB"
    assert main.hex_to_ass_color("AbCdEf") == "&H00EFCDAB"


def test_hex_color_rejects_non_hex_characters() -> None:
    """``#ZZZZZZ`` 長度合法但字元非法 → 應該拋 ValueError，而非靜默輸出非法色碼。

    靜默的話 libass 會把 ``&H00ZZZZZZ`` 解成黑色，用戶只見到「顏色冇效」，
    查唔到原因（審查報告 §2.10）。
    """
    with pytest.raises(ValueError):
        main.hex_to_ass_color("#ZZZZZZ")
    with pytest.raises(ValueError):
        main.hex_to_ass_color("#12345G")


def test_hex_color_error_message_is_actionable() -> None:
    """錯誤訊息要講清楚「只可含 0-9 / A-F」，唔止講長度。"""
    with pytest.raises(ValueError) as info:
        main.hex_to_ass_color("#ZZZZZZ")
    assert "0-9" in str(info.value) and "A-F" in str(info.value)


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


def test_generate_warns_when_audio_is_silent(tmp_path: Path, monkeypatch,
                                            caplog) -> None:
    """9.2-1 接線：偵測到全靜音要**警告**而唔係 raise（全靜音 MV 可能係刻意）。

    ⚠️ 這條防的是「函式寫好了但冇人叫」—— 審查報告 §2.3 的
    ``ThumbCache.cancelled`` 就是同類問題（檢查邏輯存在、全庫無一處設 True）。
    所以這裡不看 ``detect_silent_wav`` 的回傳值，而是看 generate 管線
    真的**走過**這個檢查點並留下警告。
    """
    import logging

    video = tmp_path / "mv.mp4"
    video.write_bytes(b"fake")
    wav = write_wav(tmp_path / "silent.wav", sine_samples(0, 0.5))

    # lambda 要食新加入嘅可選路徑參數（UI 注入用）
    monkeypatch.setattr(main, "find_ffmpeg", lambda *a, **k: "ffmpeg")
    monkeypatch.setattr(main, "find_ffprobe", lambda *a, **k: "ffprobe")
    monkeypatch.setattr(main, "probe_video_size", lambda *a, **k: (1920, 1080))
    monkeypatch.setattr(main, "extract_temp_audio", lambda *a, **k: wav)

    reached: dict[str, bool] = {}

    def stop_after_check(uploaded_wav):        # noqa: ANN001
        reached["upload"] = True
        raise main.AsrError("測試用：到上傳為止")

    monkeypatch.setattr(main, "upload_audio", stop_after_check)

    opts = main.GenerateOptions(input=video, api_key="sk-test")
    with caplog.at_level(logging.WARNING, logger="karaoke"):
        with pytest.raises(main.AsrError):
            main.generate_timings(opts)

    assert reached.get("upload") is True, "管線未走到上傳階段，測試前提不成立"
    assert any("全靜音" in rec.getMessage() for rec in caplog.records), \
        "偵測到全靜音但冇警告 —— 檢查點冇接線"


# ---------------------------------------------------------------------------
# portable ffmpeg：UI 由 settings.json 注入嘅路徑一定要壓過 PATH 偵測
# ---------------------------------------------------------------------------


def test_find_ffmpeg_prefers_injected_path(tmp_path: Path) -> None:
    """明確注入嘅 portable 路徑優先於任何 PATH／常見安裝位結果。"""
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"MZ")
    assert main.find_ffmpeg(str(exe)) == str(exe)


def test_find_ffmpeg_invalid_injected_path_raises(tmp_path: Path) -> None:
    """注入咗但檔案唔存在 → 明確報錯，唔好跌落 PATH 扮冇事。"""
    with pytest.raises(main.ToolMissingError, match="指定的 ffmpeg 路徑唔存在"):
        main.find_ffmpeg(str(tmp_path / "missing.exe"))


def test_find_ffprobe_injected_and_sibling(tmp_path: Path, monkeypatch) -> None:
    """注入 probe 直接用；淨係注入 ffmpeg 時用同目錄兄弟檔，唔再查 PATH。"""
    ffmpeg = tmp_path / "ffmpeg.exe"
    probe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"MZ")
    probe.write_bytes(b"MZ")

    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    assert main.find_ffprobe(str(probe), str(ffmpeg)) == str(probe)

    def no_fallback_ffmpeg(*a, **k):  # noqa: ANN002, ANN003
        pytest.fail("已有注入 ffmpeg，唔應該再重新偵測")

    monkeypatch.setattr(main, "find_ffmpeg", no_fallback_ffmpeg)
    assert main.find_ffprobe(None, str(ffmpeg)) == str(probe)


def test_burn_video_passes_injected_tools(monkeypatch, tmp_path: Path) -> None:
    """burn_video 要將 UI 注入嘅路徑一路傳到 burn_subtitles。"""
    video = tmp_path / "mv.mp4"
    ass = tmp_path / "mv.ass"
    out = tmp_path / "out.mp4"
    video.write_bytes(b"fake")
    ass.write_text("", encoding="utf-8")

    seen: dict[str, object] = {}

    def fake_find_ffmpeg(path=None):  # noqa: ANN001
        seen["ffmpeg_arg"] = path
        return "TOOL_FFMPEG"

    def fake_find_ffprobe(path=None, ffmpeg=None):  # noqa: ANN001
        seen["ffprobe_arg"] = path
        seen["ffprobe_base"] = ffmpeg
        return "TOOL_FFPROBE"

    monkeypatch.setattr(main, "find_ffmpeg", fake_find_ffmpeg)
    monkeypatch.setattr(main, "find_ffprobe", fake_find_ffprobe)
    monkeypatch.setattr(main, "read_playres_from_ass", lambda *a: (1920, 1080))
    monkeypatch.setattr(main, "probe_audio_codec", lambda *a: None)
    monkeypatch.setattr(main, "probe_duration_ms", lambda *a: 1000)
    monkeypatch.setattr(main, "probe_has_video", lambda *a: True)
    monkeypatch.setattr(main, "burn_subtitles", lambda **kw: seen.update(burn=kw))

    result = main.burn_video(
        video, ass, out,
        ffmpeg=r"C:\portable\ffmpeg.exe",
        ffprobe=r"C:\portable\ffprobe.exe",
    )

    assert result == out
    assert seen["ffmpeg_arg"] == r"C:\portable\ffmpeg.exe"
    assert seen["ffprobe_arg"] == r"C:\portable\ffprobe.exe"
    assert seen["ffprobe_base"] == "TOOL_FFMPEG"
    assert seen["burn"]["ffmpeg"] == "TOOL_FFMPEG"


def test_generate_timings_passes_injected_tools(tmp_path: Path, monkeypatch) -> None:
    """GenerateOptions 嘅工具路徑要傳入解析層；CLI 唔填就保持 None。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"fake")
    wav = write_wav(tmp_path / "a.wav", sine_samples(0, 0.2))

    seen: dict[str, object] = {}

    def fake_find_ffmpeg(path=None):  # noqa: ANN001
        seen["ffmpeg"] = path
        return "ffmpeg"

    def fake_find_ffprobe(path=None, ffmpeg=None):  # noqa: ANN001
        seen["ffprobe"] = path
        seen["base"] = ffmpeg
        return "ffprobe"

    monkeypatch.setattr(main, "find_ffmpeg", fake_find_ffmpeg)
    monkeypatch.setattr(main, "find_ffprobe", fake_find_ffprobe)
    monkeypatch.setattr(main, "probe_has_video", lambda *a: True)
    monkeypatch.setattr(main, "probe_video_size", lambda *a, **k: (640, 360))
    monkeypatch.setattr(main, "extract_temp_audio", lambda *a, **k: wav)

    def stop_after_check(uploaded_wav):  # noqa: ANN001
        seen["upload"] = True
        raise main.AsrError("測試用：到上傳為止")

    monkeypatch.setattr(main, "upload_audio", stop_after_check)

    opts = main.GenerateOptions(
        input=video, api_key="sk-test",
        ffmpeg_path="C:/portable/ffmpeg.exe",
        ffprobe_path="C:/portable/ffprobe.exe",
    )
    with pytest.raises(main.AsrError):
        main.generate_timings(opts)

    assert seen["ffmpeg"] == "C:/portable/ffmpeg.exe"
    assert seen["ffprobe"] == "C:/portable/ffprobe.exe"
    assert seen["base"] == "ffmpeg"
    assert seen["upload"] is True
