# -*- coding: utf-8 -*-
"""audio — 音訊引擎（計劃書 §6.6 / C2 決定：音訊同步 + 波形）。

管線：
  1. 解碼：``ffmpeg -i <video> -vn -ac 1 -ar 16000 -c:a pcm_s16le <wav>``
     （16k 單聲道：體積細、peak 位唔會變）
  2. 提 peak：numpy 讀 PCM → 分 bin 取 max(abs) → 時間軸畫 filled polygon
  3. 播放：pygame.mixer（統一 16-bit PCM WAV 最穩，R3）；唔裝 pygame 就**靜音降級**，
     編輯器其他功能全部照用（§6.6 降級路徑）。

threading：解碼喺背景 thread 跑（大片唔好卡 UI）；播放控制全部主 thread。
"""

from __future__ import annotations

import logging
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Callable

import numpy as np

LOGGER = logging.getLogger("karaoke.ui.audio")

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2      # pcm_s16le
PEAK_BINS_PER_SEC = 200


def decode_wav(ffmpeg: str, video: Path, out_wav: Path) -> Path | None:
    """影片 → 16kHz 單聲道 WAV。失敗返 None（唔拋，畀 UI 顯示訊息）。"""
    proc = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(video), "-vn",
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-acodec", "pcm_s16le", str(out_wav)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not out_wav.is_file():
        LOGGER.error("ffmpeg 抽音失敗：%s", (proc.stderr or "")[-500:])
        return None
    return out_wav


def extract_peaks(pcm_bytes: bytes, bins_per_sec: int = PEAK_BINS_PER_SEC) -> tuple[np.ndarray, int]:
    """PCM s16le bytes → peak 陣列（每 bin = max(abs)）+ 總時長 ms。V5 測試對象。"""
    samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    bin_size = SAMPLE_RATE // bins_per_sec
    n_bins = max(1, math.ceil(len(samples) / bin_size))
    pad = n_bins * bin_size - len(samples)
    if pad > 0:
        samples = np.pad(samples, (0, pad))
    peaks = np.abs(samples.reshape(n_bins, bin_size)).max(axis=1)
    duration_ms = int(len(samples) / SAMPLE_RATE * 1000)
    return peaks, duration_ms


def make_sine_wav(path: Path, freq_hz: float, seconds: float,
                  sample_rate: int = SAMPLE_RATE) -> None:
    """造正弦波 WAV（V5 測試用）。"""
    n = int(sample_rate * seconds)
    frames = bytearray()
    for i in range(n):
        v = int(32767 * 0.8 * math.sin(2 * math.pi * freq_hz * i / sample_rate))
        frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(SAMPLE_WIDTH)
        f.setframerate(sample_rate)
        f.writeframes(bytes(frames))


class AudioEngine:
    """播放控制（pygame）+ peak 快取。冇 pygame 就 available=False。"""

    def __init__(self) -> None:
        self.available = False
        self._pcm: bytes = b""
        self.duration_ms = 0
        self.peaks: np.ndarray | None = None
        self._playing = False
        self._play_t0 = 0.0
        self._start_offset_ms = 0
        self._sound = None
        self._channel = None
        try:
            import pygame
            pygame.mixer.init(frequency=SAMPLE_RATE, channels=1)   # R3：統一 16-bit PCM
            self._pygame = pygame
            self.available = True
        except Exception as exc:   # noqa: BLE001 — 冇 pygame / mixer 起唔到
            LOGGER.warning("pygame 初始化失敗，靜音降級：%s", exc)

    # ------------------------------------------------------------------
    # 載入（背景 thread 呼叫）
    # ------------------------------------------------------------------

    def load(self, wav_path: Path) -> bool:
        """讀 WAV → PCM bytes + peaks。"""
        with wave.open(str(wav_path), "rb") as f:
            self._pcm = f.readframes(f.getnframes())
        self.peaks, self.duration_ms = extract_peaks(self._pcm)
        LOGGER.info("音訊載入完成：%.1f 秒 / %d peaks",
                    self.duration_ms / 1000, len(self.peaks))
        return True

    def load_video_async(self, ffmpeg: str, video: Path,
                         on_done: Callable[["AudioEngine | None"], None],
                         on_progress: Callable[[str], None] | None = None) -> None:
        """背景解碼 + 提 peak，完成後 callback（UI thread 負責 after(0) 轉返主 thread）。"""
        def worker() -> None:
            try:
                if on_progress:
                    on_progress("抽取音訊中…")
                with tempfile.TemporaryDirectory(prefix="karaoke_ui_") as tmp:
                    wav = decode_wav(ffmpeg, video, Path(tmp) / "audio16k.wav")
                    if wav is None:
                        on_done(None)
                        return
                    if on_progress:
                        on_progress("計算波形…")
                    self.load(wav)
                on_done(self)
            except Exception as exc:   # noqa: BLE001
                LOGGER.error("音訊載入失敗：%s", exc)
                on_done(None)
        import threading
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # 播放控制（主 thread）
    # ------------------------------------------------------------------

    def play(self, from_ms: int) -> None:
        if not self.available or not self._pcm:
            return
        self.stop()
        offset = max(0, min(from_ms, self.duration_ms - 1))
        byte_off = offset * SAMPLE_RATE // 1000 * SAMPLE_WIDTH
        self._sound = self._pygame.mixer.Sound(buffer=self._pcm[byte_off:])
        self._channel = self._sound.play()
        self._playing = True
        self._play_t0 = __import__("time").monotonic()
        self._start_offset_ms = offset

    def play_range(self, start_ms: int, end_ms: int) -> None:
        """播指定範圍（「重播 3 秒」跟唱用：[游標−1s, 游標+2s]）。"""
        if not self.available or not self._pcm:
            return
        self.stop()
        start = max(0, min(start_ms, self.duration_ms - 1))
        end = max(start + 1, min(end_ms, self.duration_ms))
        b0 = start * SAMPLE_RATE // 1000 * SAMPLE_WIDTH
        b1 = end * SAMPLE_RATE // 1000 * SAMPLE_WIDTH
        self._sound = self._pygame.mixer.Sound(buffer=self._pcm[b0:b1])
        self._channel = self._sound.play()
        self._playing = True
        self._play_t0 = __import__("time").monotonic()
        self._start_offset_ms = start

    def seek(self, ms: int) -> None:
        """游標跳轉（時間軸拖曳 / ⏮⏭ / 主頁 seek 共用）。

        播放中 → 由新位置重新起播（拖曳會連續重啟，聽感係「跟手跳段」，
        §6.6 粒度 10-20ms 對 40-100ms 字級夠用）；停咗 → 只記住起點。
        """
        if not self.available or not self._pcm:
            return
        ms = max(0, min(ms, self.duration_ms - 1))
        if self._playing:
            self.play(ms)
        else:
            self._start_offset_ms = ms

    def stop(self) -> None:
        if self.available and self._channel is not None:
            try:
                self._channel.stop()
            except Exception:   # noqa: BLE001
                pass
        self._playing = False

    def toggle(self, cursor_ms: int) -> bool:
        """播放 / 暫停。回傳播放中狀態。"""
        if self._playing:
            self.stop()
            return False
        self.play(cursor_ms)
        return True

    def position_ms(self) -> int:
        """而家播到邊（播放中先有意義）。"""
        if not self._playing:
            return self._start_offset_ms
        import time
        return self._start_offset_ms + int((time.monotonic() - self._play_t0) * 1000)

    @property
    def playing(self) -> bool:
        return self._playing
