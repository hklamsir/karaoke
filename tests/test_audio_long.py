# -*- coding: utf-8 -*-
"""§2.6 —— AudioEngine 長片記憶體（全量 PCM + play() 再複製一份）。

量化背景：16 kHz / 16-bit / mono ≈ 32 KB/s

| 音訊長度 | 全量 PCM | play(0) 後峰值 |
|---|---|---|
| 5 分鐘 | ~9 MB | ~18 MB（可接受）|
| 60 分鐘 | ~115 MB | ~230 MB（32-bit Python 直接 OOM）|

本工具明確支援 30 分鐘以上長片（``ui/thumbs.py`` 的 ``LONG_VIDEO_S = 1800``），
所以真係會踩到。對策：超過 ``MAX_PCM_SECONDS`` 只逐段掃 peaks（200/s，
一小時 ≈ 2.9 MB），唔保留 PCM，播放降級為外部播放器。

測試用 monkeypatch 把門檻調到 2 秒，避免真的造一條 30 分鐘 WAV（57 MB）。
"""

from __future__ import annotations

import array
import math
import wave
from pathlib import Path

import pytest

pytest.importorskip("numpy")                 # ui.audio 在 module level import numpy

from ui import audio


def _write_sine_wav(path: Path, seconds: float, freq: float = 440.0) -> Path:
    """造 16 kHz mono s16le WAV（同 ffmpeg 抽音輸出格式一致）。"""
    n = int(audio.SAMPLE_RATE * seconds)
    samples = array.array(
        "h", (int(32767 * 0.5 * math.sin(2 * math.pi * freq * i / audio.SAMPLE_RATE))
              for i in range(n)))
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(audio.SAMPLE_WIDTH)
        f.setframerate(audio.SAMPLE_RATE)
        f.writeframes(samples.tobytes())
    return path


def test_long_audio_drops_pcm_keeps_peaks(tmp_path: Path, monkeypatch) -> None:
    """長片：只留 peaks，``_pcm`` 必須清空（唔係「留一份但唔用」）。"""
    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 2)
    wav = _write_sine_wav(tmp_path / "long.wav", 4.0)

    engine = audio.AudioEngine()
    assert engine.load(wav) is True

    assert engine.playback_degraded is True
    assert engine._pcm == b"", "長片仍然保留整段 PCM → 記憶體翻倍問題未解決"


def test_long_audio_peaks_cover_whole_timeline(tmp_path: Path, monkeypatch) -> None:
    """降級模式畫波形要準：peaks 數量 = 時長 × 200/s，時間軸才對得上。

    ⚠️ 這條防的是「串流讀取只讀到第一段」的低級錯誤 —— 波形仍然畫得出，
    但只有頭幾秒有內容，用戶會以為整條片都是靜音。
    """
    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 2)
    wav = _write_sine_wav(tmp_path / "long.wav", 4.0)

    engine = audio.AudioEngine()
    engine.load(wav)

    expected = 4 * audio.PEAK_BINS_PER_SEC
    assert len(engine.peaks) == pytest.approx(expected, abs=2)
    assert engine.duration_ms == pytest.approx(4000, abs=20)
    assert float(engine.peaks.max()) > 0.3, "peaks 全是 0 → 串流讀取失敗"


def test_long_audio_memory_is_bounded(tmp_path: Path, monkeypatch) -> None:
    """peaks 佔用要遠小於 PCM（一小時 ≈ 2.9 MB vs 115 MB）。"""
    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 2)
    wav = _write_sine_wav(tmp_path / "long.wav", 6.0)

    engine = audio.AudioEngine()
    engine.load(wav)

    pcm_equivalent = 6.0 * audio.SAMPLE_RATE * audio.SAMPLE_WIDTH    # 若保留整段
    assert engine.peaks.nbytes < pcm_equivalent / 30


def test_short_audio_keeps_pcm(tmp_path: Path, monkeypatch) -> None:
    """短片：行為完全不變（仍然保留 PCM，可以播放）。"""
    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 30)
    wav = _write_sine_wav(tmp_path / "short.wav", 1.0)

    engine = audio.AudioEngine()
    engine.load(wav)

    assert engine.playback_degraded is False
    assert len(engine._pcm) == audio.SAMPLE_RATE * audio.SAMPLE_WIDTH
    assert len(engine.peaks) == audio.PEAK_BINS_PER_SEC
    assert engine.duration_ms == pytest.approx(1000, abs=20)


def test_load_resets_degraded_flag(tmp_path: Path, monkeypatch) -> None:
    """先載長片（降級）再載短片 → 標記必須復原，否則短片永遠播唔到。

    這個 flag 是 instance 狀態，換片時唔重設就會「一次載過長片，以後全部冇聲」。
    """
    engine = audio.AudioEngine()

    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 2)
    engine.load(_write_sine_wav(tmp_path / "long.wav", 4.0))
    assert engine.playback_degraded is True

    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 30)
    engine.load(_write_sine_wav(tmp_path / "short.wav", 1.0))
    assert engine.playback_degraded is False
    assert engine._pcm, "換回短片後仍然冇 PCM → 播放永久失效"


def test_degraded_engine_refuses_to_play(tmp_path: Path, monkeypatch) -> None:
    """降級模式 ``play()`` 要安全返回（唔可以因為 ``_pcm`` 為空而拋錯）。"""
    monkeypatch.setattr(audio, "MAX_PCM_SECONDS", 2)
    engine = audio.AudioEngine()
    engine.load(_write_sine_wav(tmp_path / "long.wav", 4.0))

    engine.play(0)              # 唔應該拋任何例外
    engine.play_range(0, 1000)
    engine.seek(1000)
    engine.stop()
    assert engine.playing is False
