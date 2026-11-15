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

#: 拖曳 seek 時，兩次「重啟播放」之間最少相隔幾秒（UI-S14）。
#: play() 每次都要複製整段 PCM 尾巴，30–60Hz 重啟會造成分配風暴；
#: 120ms 對聽感（「跟手跳段」）幾乎無感，但把重啟次數壓到 ~8 次/秒。
SEEK_RESTART_MIN_S = 0.12


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
        #: 載入序號（單調遞增）：換片時舊 worker 的解碼結果必須作廢，否則
        #: 兩個 worker 會交錯寫入 peaks / duration，出現「波形 A 片、時長 B 片」
        #: 的撕裂狀態（2026-11-12 審查報告 UI-S3）。
        self._load_token = 0
        #: 拖曳 seek 的待處理位置與上次重啟時間（節流用，UI-S14）
        self._pending_seek_ms: int | None = None
        self._last_seek_restart = 0.0
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

    def load(self, wav_path: Path, *, token: int | None = None) -> bool:
        """讀 WAV → PCM bytes + peaks。 mixer 實際規格同 16k mono 唔一致就轉換。

        §2.6：超過 :data:`MAX_PCM_SECONDS` 的長片改走「只載 peaks」路徑，
        唔保留整段 PCM（見 :meth:`_scan_peaks`）。

        Args:
            wav_path: ffmpeg 抽出的 16 kHz 單聲道 s16le WAV。
            token: 載入序號（見 ``_load_token``）。解碼前後都會核對，被更新的
                ``load_video_async`` 取代就丟棄結果 —— 唔會出現「peaks 來自
                A 片、duration 來自 B 片」的撕裂狀態（2026-11-12 報告 UI-S3）。

        Returns:
            True 表示載入成功並已套用；False 表示被新任務取代（結果已丟棄）。
        """
        if token is not None and token != self._load_token:
            return False
        with wave.open(str(wav_path), "rb") as f:
            n_frames = f.getnframes()
            rate = f.getframerate() or SAMPLE_RATE
            seconds = n_frames / rate if rate else 0.0
            if seconds > MAX_PCM_SECONDS:
                peaks = self._scan_peaks(f)
                pcm, duration_ms, degraded = b"", int(seconds * 1000), True
            else:
                pcm = self._convert_to_mixer_spec(
                    f.readframes(n_frames), SAMPLE_RATE, 1, SAMPLE_WIDTH)
                peaks, duration_ms = extract_peaks(pcm)
                degraded = False
        if token is not None and token != self._load_token:
            return False      # 解碼期間又被換片 → 唔好覆蓋新結果
        # 一次過套用，避免主線程讀到「新 peaks + 舊 duration」的中間狀態
        self._pcm = pcm
        self.peaks = peaks
        self.duration_ms = duration_ms
        self.playback_degraded = degraded
        if degraded:
            LOGGER.info("長片 %.0f 分鐘：只載入 %d 個 peaks（%.1f MB），播放降級為外部播放器",
                        duration_ms / 60000, len(peaks), peaks.nbytes / 1_048_576)
        LOGGER.info("音訊載入完成：%.1f 秒 / %d peaks",
                    duration_ms / 1000, len(peaks))
        return True

    def _scan_peaks(self, wav_file: wave.Wave_read) -> np.ndarray:
        """長片：逐段掃 peaks，唔保留 PCM（§2.6）；回傳 peak 陣列。

        每次讀 5 秒（80,000 samples = 1000 bins，剛好整除 bin_size），
        所以除最後一段外唔會有 padding，串接後的 bin 對齊等同一次過處理。

        Args:
            wav_file: 已開啟的 WAV（呼叫方負責關閉）。

        Returns:
            串接後的 peak 陣列（空檔回傳長度 1 的零陣列）。
        """
        chunk_frames = SAMPLE_RATE * 5
        parts: list[np.ndarray] = []
        while True:
            raw = wav_file.readframes(chunk_frames)
            if not raw:
                break
            chunk_peaks, _ = extract_peaks(raw)
            parts.append(chunk_peaks)
        return np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)

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
        """背景解碼 + 提 peak，完成後 callback（UI thread 負責 after(0) 轉返主 thread）。

        每次呼叫遞增 ``_load_token``；舊 worker 完成時若發現序號已變，就靜靜
        放棄（唔覆蓋新片狀態、唔覆蓋新任務的進度文字、亦唔觸發 ``on_done``
        —— 否則會把新片的「抽取中」狀態誤標成失敗）。見報告 UI-S3。
        """
        self._load_token += 1
        token = self._load_token

        def report(msg: str) -> None:
            if on_progress is not None and token == self._load_token:
                on_progress(msg)

        def worker() -> None:
            try:
                report("抽取音訊中…")
                with tempfile.TemporaryDirectory(prefix="karaoke_ui_") as tmp:
                    wav = decode_wav(ffmpeg, video, Path(tmp) / "audio16k.wav")
                    if token != self._load_token:
                        return
                    if wav is None:
                        on_done(None)
                        return
                    report("計算波形…")
                    if not self.load(wav, token=token):
                        return      # 解碼期間已被更新的換片取代
                if token != self._load_token:
                    return
                on_done(self)
            except Exception as exc:   # noqa: BLE001
                LOGGER.error("音訊載入失敗：%s", exc)
                if token == self._load_token:
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
        self._pending_seek_ms = None
        # 播完之後游標停在片尾（= duration_ms）；此時再按播放應由頭開始，
        # 否則會被下面 min(...) 鉗到最後 1ms，聽感等同「按了沒反應」。
        if self.duration_ms > 0 and from_ms >= self.duration_ms:
            from_ms = 0
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

        播放中 → 由新位置重新起播，但**節流**至每 :data:`SEEK_RESTART_MIN_S`
        一次：``play()`` 會複製整段 PCM 尾巴（長片可達幾十 MB），拖曳時
        30–60Hz 重啟會造成分配風暴（2026-11-12 審查報告 UI-S14）。被節流
        掉的位置記在 ``_pending_seek_ms``，由 30ms 輪詢的 ``position_ms()``
        補上，所以放手後最終位置仍然準確。
        """
        if not self.available or not self._pcm:
            return
        ms = max(0, min(ms, self.duration_ms - 1))
        if self.playing:      # property 會先結算「自然播完」，避免誤判仍在播
            self._pending_seek_ms = ms
            self._flush_pending_seek()
        else:
            self._pending_seek_ms = None
            self._start_offset_ms = ms

    def _flush_pending_seek(self) -> None:
        """節流窗口過了才真正重啟播放（UI-S14）。"""
        if self._pending_seek_ms is None:
            return
        import time
        now = time.monotonic()
        if now - self._last_seek_restart < SEEK_RESTART_MIN_S:
            return
        ms = self._pending_seek_ms
        self._pending_seek_ms = None
        self._last_seek_restart = now
        self.play(ms)

    def stop(self) -> None:
        if self.available and self._channel is not None:
            try:
                self._channel.stop()
            except Exception:   # noqa: BLE001
                pass
        self._playing = False
        self._pending_seek_ms = None

    def toggle(self, cursor_ms: int) -> bool:
        """播放 / 暫停。回傳播放中狀態。"""
        if self.playing:      # property 先結算自然播完，否則會「按一下只係停止」
            self.stop()
            return False
        self.play(cursor_ms)
        return True

    def position_ms(self) -> int:
        """現在播到哪裡（毫秒）；永遠不會超出片長。

        播放中先結算「聲音是否已自然播完」，再補上被節流的 seek，最後以
        單調時鐘推算位置；若剛好越過片尾則鉗到 ``duration_ms``。
        """
        self._settle_if_finished()
        self._flush_pending_seek()
        if not self._playing:
            return self._start_offset_ms
        import time
        pos = self._start_offset_ms + int((time.monotonic() - self._play_t0) * 1000)
        return max(0, min(pos, self.duration_ms))

    def _settle_if_finished(self) -> None:
        """聲音自然播完 → 清 ``_playing``（2026-11-12 審查報告 UI-B2）。

        pygame 的 channel 播完後 ``get_busy()`` 變 False，但本引擎以前從不
        查詢，令 ``_playing`` 永真：游標／播放頭會以真實時間速度衝出片尾，
        播放鍵永遠顯示 ⏸。這裡集中處理「自然結束」，不要在呼叫方各自補。
        """
        if not self._playing:
            return
        channel = self._channel
        if channel is None:
            self._playing = False
            return
        try:
            busy = channel.get_busy()
        except Exception:   # noqa: BLE001 — channel 已失效（mixer 重啟）
            self._playing = False
            return
        if not busy:
            self._playing = False
            # 停在片尾，令 position_ms() 之後回傳總長度（再按播放會由頭開始）
            self._start_offset_ms = max(0, self.duration_ms)

    @property
    def playing(self) -> bool:
        self._settle_if_finished()
        return self._playing
