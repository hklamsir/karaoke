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

#: 超過此長度就**唔保留**整段 PCM，只留 peaks（審查報告 §2.6）。
#: 16k / 16-bit / mono ≈ 32 KB/s → 30 分鐘 ≈ 57 MB；`play()` 的
#: `Sound(buffer=pcm[byte_off:])` 會再複製整段尾端，峰值翻倍。
#: peaks 只有 200/s（一小時 ≈ 2.9 MB），畫波形完全夠用。
MAX_PCM_SECONDS = 30 * 60


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
        #: True = 長片降級：只有 peaks，冇 PCM，播放停用（§2.6）
        self.playback_degraded = False
        try:
            import pygame
            # R3：統一 16-bit PCM 16k 單聲道。
            # ⚠️ allowedchanges=0 必須：SDL 預設允許裝置改頻率/聲道（AUDIO_ALLOW_*），
            #    實測 16k mono 請求會被開成 48k stereo → 16k data 被當 48k stereo 播
            #    = 3 倍速 + 怪聲。allowedchanges=0 強制 SDL 內部轉換到精確規格。
            pygame.mixer.init(frequency=SAMPLE_RATE, size=-16, channels=1,
                              allowedchanges=0)
            got = pygame.mixer.get_init()
            if got is None or got[0] != SAMPLE_RATE or got[2] != 1:
                LOGGER.warning("mixer 實際規格 %r ≠ 預期 (16000, -16, 1)；load() 會自動轉換 PCM", got)
            self._pygame = pygame
            self.available = True
        except Exception as exc:   # noqa: BLE001 — 冇 pygame / mixer 起唔到
            LOGGER.warning("pygame 初始化失敗，靜音降級：%s", exc)

    # ------------------------------------------------------------------
    # 載入（背景 thread 呼叫）
    # ------------------------------------------------------------------

    def load(self, wav_path: Path) -> bool:
        """讀 WAV → PCM bytes + peaks。 mixer 實際規格同 16k mono 唔一致就轉換。

        §2.6：超過 :data:`MAX_PCM_SECONDS` 的長片改走「只載 peaks」路徑，
        唔保留整段 PCM（見 :meth:`_load_peaks_only`）。

        Args:
            wav_path: ffmpeg 抽出的 16 kHz 單聲道 s16le WAV。

        Returns:
            True 表示載入成功（降級模式亦算成功 —— 波形照畫，只係唔可以播）。
        """
        self.playback_degraded = False
        with wave.open(str(wav_path), "rb") as f:
            n_frames = f.getnframes()
            rate = f.getframerate() or SAMPLE_RATE
            seconds = n_frames / rate if rate else 0.0
            if seconds > MAX_PCM_SECONDS:
                return self._load_peaks_only(f, seconds)
            self._pcm = f.readframes(n_frames)
        self._pcm = self._convert_to_mixer_spec(self._pcm, SAMPLE_RATE, 1, SAMPLE_WIDTH)
        self.peaks, self.duration_ms = extract_peaks(self._pcm)
        LOGGER.info("音訊載入完成：%.1f 秒 / %d peaks",
                    self.duration_ms / 1000, len(self.peaks))
        return True

    def _load_peaks_only(self, wav_file: wave.Wave_read, seconds: float) -> bool:
        """長片：逐段掃 peaks，唔保留 PCM（§2.6）。

        每次讀 5 秒（80,000 samples = 1000 bins，剛好整除 bin_size），
        所以除最後一段外唔會有 padding，串接後的 bin 對齊等同一次過處理。

        Args:
            wav_file: 已開啟的 WAV（呼叫方負責關閉）。
            seconds: 總時長，用於 ``duration_ms``。

        Returns:
            恆為 True。
        """
        chunk_frames = SAMPLE_RATE * 5
        parts: list[np.ndarray] = []
        while True:
            raw = wav_file.readframes(chunk_frames)
            if not raw:
                break
            chunk_peaks, _ = extract_peaks(raw)
            parts.append(chunk_peaks)
        self.peaks = np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)
        self._pcm = b""                      # 關鍵：唔保留整段 PCM
        self.duration_ms = int(seconds * 1000)
        self.playback_degraded = True
        LOGGER.info("長片 %.0f 分鐘：只載入 %d 個 peaks（%.1f MB），播放降級為外部播放器",
                    seconds / 60, len(self.peaks), self.peaks.nbytes / 1_048_576)
        return True

    def _convert_to_mixer_spec(self, pcm: bytes, src_freq: int, src_channels: int,
                               src_width: int) -> bytes:
        """PCM 轉換到 mixer 實際規格（防 allowedchanges 談判唔到 16k mono）。

        線性插值重採樣 + mono→N 聲道複製。規格一致就原樣返（零開銷）。
        """
        if not self.available:
            return pcm
        try:
            init = self._pygame.mixer.get_init()
        except Exception:   # noqa: BLE001
            return pcm
        if init is None:
            return pcm
        dst_freq, dst_size, dst_channels = init
        if dst_freq == src_freq and dst_channels == src_channels and abs(dst_size) == src_width:
            return pcm
        LOGGER.info("mixer 規格 %r ≠ 16k mono，轉換 PCM（一次性）", init)
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if src_channels > 1:   # 先摺做 mono
            samples = samples.reshape(-1, src_channels).mean(axis=1)
        # 線性插值重採樣
        n_src = len(samples)
        n_dst = int(round(n_src * dst_freq / src_freq))
        if n_dst > 0 and n_dst != n_src:
            x_src = np.linspace(0.0, n_src - 1, num=n_dst)
            samples = np.interp(x_src, np.arange(n_src), samples)
        out = samples.astype(np.int16).tobytes()
        if dst_channels > 1:   # mono → N 聲道交錯複製
            frames = np.frombuffer(out, dtype=np.int16)
            out = np.repeat(frames, dst_channels).tobytes()
        return out

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
