# -*- coding: utf-8 -*-
"""fixtures.media — 用 ffmpeg lavfi 即場生成測試影音素材。

**為何唔入庫二進位**：這些素材全部係 ffmpeg 合成（``testsrc2`` 畫面 + ``sine`` 音調），
任何機器 2 秒內可重生同一個檔。commit 10MB 二進位落 git 歷史（不可刪）換取的
只是「省一次 ffmpeg 呼叫」，並不值得。

配方來源：``_probe/probe_burn_source.py::make_fixtures()``（原為人手 probe，
每次執行都覆寫 `_smoke/burn_source/`，本質是 cache 而非資產）。

素材零第三方版權內容 —— 純色條 + 正弦波，冇任何歌詞或 MV 畫面。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

#: 容許測試環境指定 ffmpeg（例如 Windows 上 ffmpeg 唔在 PATH）
FFMPEG_ENV = "KARAOKE_FFMPEG"

#: 預設素材規格（與 _probe 一致，方便對照舊紀錄）
ORIG_SIZE = "640x360"
SQUARE_SIZE = "480x480"
DEFAULT_SECONDS = 10


def ffmpeg_path() -> str | None:
    """搵 ffmpeg：先看 ``KARAOKE_FFMPEG``，再找 PATH。

    ⚠️ 刻意唔用 ``main.find_ffmpeg()`` —— 它在找不到時 ``sys.exit(2)``，
    會令測試變成「整體中斷」而非「skip」。
    """
    override = os.environ.get(FFMPEG_ENV, "").strip()
    if override and Path(override).is_file():
        return override
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    """搵 ffprobe（通常與 ffmpeg 同目錄）。"""
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe")


def _run(cmd: list[str]) -> None:
    """執行外部指令；失敗即拋（測試寧願紅，唔要靜默產出半截素材）。"""
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(
            f"素材生成失敗（rc={proc.returncode}）：\n  {' '.join(cmd)}\n{(proc.stderr or '')[-800:]}"
        )


def make_video(
    out: Path,
    *,
    seconds: int = DEFAULT_SECONDS,
    size: str = ORIG_SIZE,
    freq: int = 440,
) -> Path:
    """生成一段「彩色測試畫面 + 正弦音調」的 mp4。

    Args:
        out: 輸出路徑。
        seconds: 時長（秒）。
        size: ``WxH``。
        freq: 音調頻率（Hz）；用不同頻率令不同素材聽得出分別。

    Returns:
        輸出路徑。
    """
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg")
    _run([
        ffmpeg, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-shortest", str(out),
    ])
    return out


def make_wav(out: Path, *, seconds: int = DEFAULT_SECONDS, freq: int = 330) -> Path:
    """生成純音檔（無視訊軌）—— 用於驗「揀到音檔 → 不可燒錄」。"""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("找不到 ffmpeg")
    _run([
        ffmpeg, "-y", "-v", "error", "-f", "lavfi",
        "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}",
        "-c:a", "pcm_s16le", str(out),
    ])
    return out


def write_ass(
    path: Path,
    *,
    play_res_x: int = 640,
    play_res_y: int = 360,
    text: str = "測試",
) -> Path:
    """寫一個最小可用的 ASS（每個字 200ms，``\\kf`` 逐字高亮）。

    Args:
        path: 輸出路徑。
        play_res_x: 座標系寬（須與影片一致，否則字幕位置偏移）。
        play_res_y: 座標系高。
        text: 要顯示的字。

    Returns:
        輸出路徑。
    """
    pieces = "".join("{\\kf20}" + ch for ch in text)
    body = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {play_res_x}\n"
        f"PlayResY: {play_res_y}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Karaoke,Microsoft JhengHei,48,&H0000FFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,3.0,0,2,20,20,20,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.20,0:00:08.80,Karaoke,,0,0,0,,{pieces}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def build_all(workdir: Path) -> dict[str, Path]:
    """一次生成整套素材（配方與 ``_probe/probe_burn_source.py`` 一致）。

    Args:
        workdir: 輸出目錄（通常係 pytest 的 tmp 目錄）。

    Returns:
        ``{"orig", "same", "short", "square", "wav", "ass"}`` 路徑字典。
    """
    workdir.mkdir(parents=True, exist_ok=True)
    return {
        "orig": make_video(workdir / "orig.mp4", size=ORIG_SIZE, freq=440),
        # 同長度、同比例 → 應該零警告
        "same": make_video(workdir / "inst_same.mp4", size=ORIG_SIZE, freq=330),
        # 短 3 秒 → 應該出時長警告（門檻 DURATION_WARN_MS = 1500ms）
        "short": make_video(workdir / "inst_short.mp4", seconds=7, size=ORIG_SIZE, freq=330),
        # 16:9 → 1:1 → 應該出比例警告
        "square": make_video(workdir / "inst_square.mp4", size=SQUARE_SIZE, freq=330),
        "wav": make_wav(workdir / "music_only.wav"),
        "ass": write_ass(workdir / "orig_karaoke.ass",
                         play_res_x=640, play_res_y=360),
    }
