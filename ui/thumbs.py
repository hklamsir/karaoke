# -*- coding: utf-8 -*-
"""thumbs — 2fps 縮圖抽取 + 快取（計劃書 §6.7.4）。

⚠️ tkinter 原生 PhotoImage 唔支援 JPEG → 一律抽 **PNG**（R13，零新依賴）。
快取鍵 = sha1(絕對路徑 + mtime + size) → ~/.karaoke/thumbs/<hash>_f2/。
片長 > 30 分鐘 → 改**按需抽**（§6.7.4 降級），唔會一次過抽幾千張。
"""

from __future__ import annotations

import hashlib
import logging
import math
import subprocess
from pathlib import Path

from . import settings

LOGGER = logging.getLogger("karaoke.ui.thumbs")

FPS = 2
LONG_VIDEO_S = 1800          # 30 分鐘：超過就按需抽


def probe_duration_ms(ffprobe: str, video: Path) -> int:
    """片長（ms）；讀唔到返 0。"""
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return int(float((proc.stdout or "0").strip() or 0) * 1000)
    except (ValueError, OSError):
        return 0


def cache_key(video: Path) -> str:
    stat = video.stat()
    raw = f"{video.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


class ThumbCache:
    """2fps PNG 縮圖快取（或長片按需抽）。"""

    FPS = FPS     # class 屬性：畀 preview 之類嘅 caller 存取（例如 frame idx 換算）

    def __init__(self, video: Path, ffmpeg: str, ffprobe: str) -> None:
        self.video = video
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.duration_ms = probe_duration_ms(ffprobe, video)
        self.on_demand = self.duration_ms > LONG_VIDEO_S * 1000
        self.cache_dir = settings.thumbs_cache_dir() / f"{cache_key(video)}_f2"
        # 實測 ffmpeg fps=2 出 floor(時長×2) 張（174.03s → 348 非 349）
        self.expected = max(1, int(self.duration_ms / 1000 * FPS))
        self._single: dict[int, Path] = {}      # 按需抽嘅單幀快取（frame index → path）
        self.cancelled = False

    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        # ±1 容差：ffprobe 片長四捨五入同 ffmpeg 實際幀數可能差一張
        return self.cache_dir.is_dir() and self.count_files() >= self.expected - 1

    def count_files(self) -> int:
        if not self.cache_dir.is_dir():
            return 0
        return sum(1 for _ in self.cache_dir.glob("*.png"))

    def frame_path(self, ms: int) -> Path | None:
        """取某時間點嘅縮圖；快取冇就返 None（on_demand 模式會即場抽）。"""
        idx = max(0, min(self.expected - 1, int(ms / 1000 * FPS)))
        if self.on_demand:
            return self._single.get(idx) or self._extract_single(idx)
        path = self.cache_dir / f"{idx:05d}.png"
        return path if path.is_file() else None

    # ------------------------------------------------------------------
    # 背景抽取（thread 呼叫）
    # ------------------------------------------------------------------

    def prepare(self, on_progress=None) -> bool:
        """整批抽取（背景 thread 呼叫）。已完整就即刻 True。"""
        if self.is_complete():
            return True
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.on_demand:
            return True     # 按需模式唔整批抽
        LOGGER.info("抽縮圖：%d 張（%s）", self.expected, self.cache_dir.name)
        # -nostdin 防止 ffmpeg 食 stdin；PNG（q:v 3 → 體積細、畫質夠 strip 用）
        proc = subprocess.Popen(
            [self.ffmpeg, "-y", "-nostdin", "-v", "error", "-i", str(self.video),
             "-vf", f"fps={FPS},scale=160:-2", "-q:v", "3",
             str(self.cache_dir / "%05d.png")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # poll 目錄數量做進度（每 10 張報一次）
        last_reported = -1
        while proc.poll() is None:
            if self.cancelled:
                proc.terminate()
                return False
            n = self.count_files()
            if on_progress and n // 10 != last_reported:
                last_reported = n // 10
                on_progress(n, self.expected)
            import time
            time.sleep(0.4)
        return self.is_complete()

    def _extract_single(self, idx: int) -> Path | None:
        """按需抽單幀（L2：-ss 精準 seek，640 寬）。"""
        if self.cancelled:
            return None
        ts = idx / FPS
        out = self.cache_dir / f"s{idx:05d}.png"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if not out.is_file():
            proc = subprocess.run(
                [self.ffmpeg, "-y", "-v", "error", "-ss", f"{ts:.3f}",
                 "-i", str(self.video), "-frames:v", "1",
                 "-vf", "scale=640:-2", "-q:v", "2", str(out)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                LOGGER.warning("抽單幀失敗 @%ss：%s", ts, (proc.stderr or "")[-200:])
                return None
        self._single[idx] = out
        return out

    # ------------------------------------------------------------------

    @staticmethod
    def clear_all() -> int:
        """清快取；回傳清咗幾多 MB。"""
        import shutil
        root = settings.thumbs_cache_dir()
        total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file()) if root.is_dir() else 0
        shutil.rmtree(root, ignore_errors=True)
        return total // 1_048_576
