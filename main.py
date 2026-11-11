#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
main.py — 卡拉OK字幕產生器（阿里百煉 ASR → timings.json → ASS → ffmpeg 燒錄）

三個 subcommand：
    generate   影片 → 百煉 ASR → *_timings.json + *_karaoke.ass     （**預設唔出影片**）
    render     *_timings.json → *_karaoke.ass                        （毋須重跑 ASR）
    burn       影片 + .ass → *_karaoke.mp4                            （淨係食 .ass，冇隱含 render）

設計依據（must keep in sync）：
  - docs/名詞表.md        ：全部術語、單位換算、禁止詞
  - docs/架構決策紀錄.md  ：ADR-001 ~ ADR-032

處理管線（次序為鐵律，見 ADR-008）：
    ASR → 拆字（WordSlot→CharSlot，包成 WordGroup；標點獨立存 WordGroup.punctuation）
         → OpenCC s2hk → 斷句（**標點優先** ADR-013）→ 寫 timings.json（v2）→ 渲染 KaraokeAss

⚠️ ADR-012：LLM 歌詞校正已**徹底移除**。原因：本工具定位係「粵/國/英的**書面語**歌」，
   錯字交由日後的字幕編輯 UI 人手修正（ADR-011 實測 LLM 校正淨收益 −1~+2 字、卻要等多 2~7.5 分鐘）。
⚠️ ADR-013：斷句**標點優先**（全部句讀 + sentence 邊界；max_chars/gap 淨係安全網）；
   歌詞**預設剝走標點**（行尾刪、行中變半形空格 + 停頓），`--keep-punctuation` 還原 v1 行為。
"""

from __future__ import annotations

import argparse
import json
import logging
import array
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# ---------------------------------------------------------------------------
# 常數與預設值
# ---------------------------------------------------------------------------

DEFAULT_ASR_MODEL = "qwen-audio-3.1-asr-flash-filetrans"
DEFAULT_OPENCC_CONFIG = "s2hk"                    # ADR-008：唯一允許的 config
# v3（字幕編輯器 UI）：break_reason 新增 "manual"（UI 手動斷句）；source 新增 edited_at/edited_by
TIMINGS_SCHEMA_VERSION = 3

# ADR-013：短句合併門檻 —— **雙條件**（字數 ≤ min 且 時長 < min）同時成立先算「太短」要合併
# （2026-10-29 修訂：3字/1500ms → 5字/2500ms，實測舊門檻太保守，4-5 字快句照出會太散）
MIN_CLAUSE_CHARS = 5
MIN_CLAUSE_MS = 2500

# ADR-013 修訂：合併後嘅行**不可長過 16 字**（同 max_chars=18 分開 —— 合併係為咗避免
# 短行，唔係為咗砌長行；兜底切長句仍然用 max_chars）
MERGE_MAX_CHARS = 16

# ADR-013：標點分級 —— soft（句讀，話未講完）定 hard（句末，一個完整樂句）
# 用途：短句定向合併（hard → 向後併、soft → 向前併）
# ⚠️ v1 舊檔相容剝離**唔剝英文句號 '.'**（避免動到 Mr. 呢類縮寫，見 _TRAILING_PUNCT_RE），
#    但新跑 generate 時 ASR 畀嘅 '.' 一樣當 hard 斷行點
_PUNCT_SOFT = "，、：,:"
_PUNCT_HARD = "。！？；…!?"

# ADR-013：v1 舊檔相容 —— 尾部標點剝離用。
# ⚠️ 刻意**唔包英文句號 '.'**（避免動到 ``Mr.`` 呢類縮寫）；CJK 句讀全包
_TRAILING_PUNCT_RE = re.compile(r"[，。！？；：、…,!?:;]+$")

MS_PER_CS = 10

LOGGER = logging.getLogger("karaoke")


# ---------------------------------------------------------------------------
# 例外階層（ADR-024）
# ---------------------------------------------------------------------------
# 為何要例外化：`sys.exit()` 令「管線函式」同 CLI 綁死 —— UI 背景 thread 只能靠
# `except SystemExit` 猜發生咩事，caller 亦無法分辨「缺 ffmpeg」同「ASR 失敗」。
# 改為拋例外之後：CLI 由 main() 統一轉退出碼，UI 可以 `except KaraokeError`
# 直接拿到用戶可讀訊息。
#
# ⚠️ 不變量：每個子類的 `code` 必須**等於** v1.0.4 之前對應的 `sys.exit(N)`。
#    這是對外契約（CI 腳本、捷徑、同事的 .bat 都可能靠 exit code 判斷），
#    由 tests/test_pipeline_io.py 與 test_asr_parse.py 的例外契約測試鎖住。


class KaraokeError(Exception):
    """本工具所有「可預期錯誤」的基底類別。

    詳細的 stderr / JSON 一律交由 `LOGGER.error()` 輸出；例外訊息只放**短句**，
    因為它會直接顯示在 UI 對話框（太長會爆窗）。

    Attributes:
        code: 對應的 CLI 退出碼。子類以類別屬性覆寫，亦可在建構時用 ``code=`` 覆寫。
    """

    code: int = 1

    def __init__(self, message: str, *, code: int | None = None) -> None:
        """建立例外。

        Args:
            message: 用戶可讀的短句（不含堆疊、不含長段 stderr）。
            code: 覆寫 CLI 退出碼；``None`` 表示沿用類別的預設值。
        """
        super().__init__(message)
        if code is not None:
            self.code = code


class UsageError(KaraokeError):
    """用法／環境／輸入資料有問題（退出碼 2）。

    這類錯誤用戶改設定或參數就可以解決，不需要重跑整條管線。
    """

    code = 2


class ToolMissingError(UsageError):
    """缺少外部工具：ffmpeg / ffprobe / audio-separator（退出碼 2）。

    獨立成子類是因為訊息要教用戶**怎樣安裝**，與一般用法錯誤的處理不同。
    """

    code = 2


class AudioExtractionError(KaraokeError):
    """抽音、人聲分離或人聲標準化失敗（退出碼 3）。

    最常見成因：輸入檔損壞、容器無音軌、或 ffmpeg 不支援該 codec。
    """

    code = 3


class AsrError(KaraokeError):
    """ASR 提交、輪詢、下載或解析失敗（退出碼 4）。"""

    code = 4


class NoSubtitlesError(AsrError):
    """ASR 回傳空結果／冇 ``transcripts``，或斷句後零行（退出碼 4）。

    繼承 :class:`AsrError` 是因為成因同屬「ASR 冇畀到可用內容」，
    呼叫方可以只 catch 父類就把兩種情況一併處理。
    """

    code = 4


class BurnError(KaraokeError):
    """ffmpeg 燒錄字幕失敗（退出碼 5）。"""

    code = 5


class TaskCancelled(Exception):
    """用戶主動取消任務（ADR-025）—— **刻意唔繼承** :class:`KaraokeError`。

    若取消都算 ``KaraokeError``，CLI 入口會把它轉成非零退出碼，「我唔想等」
    就會變成「程式出錯」。UI 只需要 ``except TaskCancelled`` 顯示「已取消」，
    唔需要區分係抽音、ASR 定燒錄階段取消。
    """


# ---------------------------------------------------------------------------
# 任務互斥（審查報告 §2.3 / 9.2-12）
# ---------------------------------------------------------------------------

class SingleFlight:
    """單飛閘：保證同一時間只有一個任務在跑。

    為何唔用布林旗標：``if not self._running: self._running = True`` 係
    check-then-act，兩條 thread 同時讀到 ``False`` 就會一齊過關（TOCTOU）。
    ``Lock.acquire(blocking=False)`` 係原子操作，同一刻只有一條 thread 成功。

    Examples:
        >>> guard = SingleFlight()
        >>> guard.acquire()
        True
        >>> guard.acquire()
        False
        >>> guard.release()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held = False          # 只作 UI 顯示用，唔參與互斥判斷

    def acquire(self) -> bool:
        """嘗試取得（不阻塞）。

        Returns:
            True 表示取得成功；False 表示已經有任務在跑。
        """
        if not self._lock.acquire(blocking=False):
            return False
        self._held = True
        return True

    def release(self) -> None:
        """釋放閘門。

        Raises:
            RuntimeError: 未持有時釋放（同 :class:`threading.Lock` 一致）。
        """
        self._held = False
        self._lock.release()

    @property
    def locked(self) -> bool:
        """是否已有任務持有（僅供顯示／測試參考，唔可以用嚟做互斥判斷）。"""
        return self._held

    def __enter__(self) -> "SingleFlight":
        """進入 context；已被佔用即拋錯，避免「靜默唔跑」。"""
        if not self.acquire():
            raise RuntimeError("已經有任務在跑，唔可以重複提交")
        return self

    def __exit__(self, *exc_info: object) -> None:
        """**任何**離開路徑（含例外）都必須釋放，否則一次失敗就永久鎖死。"""
        self.release()


# ---------------------------------------------------------------------------
# 原子寫入（審查報告 §2.8）
# ---------------------------------------------------------------------------
# 為何要原子：generate 的典型用法係「重跑覆蓋舊檔」，而 Ctrl+C / 斷電會令
# write_text 留下半截檔 —— 舊 timings.json 連同人工校對成果一齊報廢。
# tmp + os.replace 保證「要麼舊檔完整、要麼新檔完整」，冇第三種狀態。

def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """原子寫入二進位內容（同目錄 tmp → ``os.replace``）。

    Args:
        path: 目標檔；父目錄不存在會自動建立。
        data: 完整內容（必須一次過寫入，唔可以分段呼叫）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> None:
    """原子寫入文字（UTF-8）。"""
    atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: str | Path, doc: dict[str, Any]) -> None:
    """原子寫入 JSON，並在覆寫前留一份 ``.bak``。

    ``.bak`` 是 UI 既有約定（``ui/models.py``），保留是因為「覆寫後才發現
    內容有問題」時，用戶仍然有上一版可以救。
    """
    path = Path(path)
    if path.is_file():
        bak = path.with_name(path.name + ".bak")
        bak.write_bytes(path.read_bytes())
    atomic_write_text(path, json.dumps(doc, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# 單位 Helper（ADR-005：全專案只准有呢兩個 function 做時間換算）
# ---------------------------------------------------------------------------

def ms_to_cs(ms: int | float) -> int:
    """毫秒 → 百分秒（ASS \\k / \\kf 的參數單位）。1 cs = 10 ms。"""
    return max(1, round(ms / MS_PER_CS))


def ms_to_ass_time(ms: int | float) -> str:
    """毫秒 → ASS Dialogue 時間戳，格式硬性為 ``H:MM:SS.cc``。

    ⚠️ ADR-005：寫成 ``M:SS:cc``（如 ``0:00:28``）時 libass 會解讀成「28 秒」，
    ffmpeg 唔會報錯退出，只會喺 verbose log 噴 ``Bad timestamp``，然後輸出
    一條**完全冇字幕、size 同輸入一樣**的影片。
    """
    cs_total = max(0, round(ms / MS_PER_CS))
    cs = cs_total % 100
    total_s = cs_total // 100
    seconds = total_s % 60
    minutes = (total_s // 60) % 60
    hours = total_s // 3600
    return f"{hours:d}:{minutes:02d}:{seconds:02d}.{cs:02d}"


#: 顏色只接受 `#RRGGBB`（可省略 #）。字元集必須驗 —— 只驗長度的話
#: `#ZZZZZZ` 會靜默產生 `&H00ZZZZZZ`，libass 解讀成黑色，用戶見到「顏色冇效」
#: 但程式唔會報錯（審查報告 §2.10）。
_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def hex_to_ass_color(hex_color: str) -> str:
    """``#RRGGBB`` → ``&HAABBGGRR``（ASS 顏色編碼，注意係 **BGR 反序**）。

    Args:
        hex_color: 6 位 hex 色碼（可帶 ``#``，大小寫不拘）。

    Returns:
        ASS 的 ``&HAABBGGRR`` 字串（alpha 固定 00，即不透明）。

    Raises:
        ValueError: 長度不是 6，或含有非 hex 字元（例如 ``#ZZZZZZ``）。
    """
    value = hex_color.strip().lstrip("#")
    if not _HEX_COLOR_RE.match(value):
        raise ValueError(
            f"顏色必須係 6 位 hex（#RRGGBB，只可含 0-9 / A-F），收到：{hex_color!r}")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H00{b}{g}{r}".upper()


def _validate_style_colors(**colors: str | None) -> None:
    """即時驗證 CLI 傳入的顏色參數（``ValueError`` → ``UsageError``）。

    為何要這一層：``hex_to_ass_color`` 拋的是 ``ValueError``（好適合做純函式），
    但 CLI 直接食 ``ValueError`` 會出 traceback 而唔係乾淨的退出碼 2 ——
    用戶見到一堆堆疊，唔知係自己打錯參數。

    Args:
        **colors: 以參數名為 key 的顏色值；``None`` 會被略過
            （``render`` 的樣式覆寫預設就是 ``None``，代表「沿用 JSON」）。

    Raises:
        UsageError: 任何不是 ``#RRGGBB`` 的值。
    """
    for name, value in colors.items():
        if value is None:
            continue
        try:
            hex_to_ass_color(value)
        except ValueError as exc:
            message = f"--{name.replace('_', '-')}：{exc}"
            # ⚠️ 拋出點必須先 LOGGER.error（見 main() 的註解）—— 否則 CLI 只會
            #    靜默回退出碼 2，用戶完全唔知發生咩事。
            LOGGER.error("%s", message)
            raise UsageError(message) from exc


#: 中日韓統一表意文字等（用於判斷要不要跑 OpenCC，見 ADR-012）
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿-﫿぀-ヿ가-힯]")

#: 標點符號（用於計算「有效歌詞字數」，例如判斷 ASR 雜訊殘句）
_PUNCT_RE = re.compile(r"[\s，。、！？：；「」『』（）《》〈〉,.\"!?:;'()\[\]…—\-~—]")


def has_cjk(text: str) -> bool:
    """文字入面有冇中日韓字元。ADR-012：OpenCC 只喺 True 時先跑。"""
    return bool(_CJK_RE.search(text))


# ---------------------------------------------------------------------------
# 資料結構（名詞表第 3 節 B 組）
# ---------------------------------------------------------------------------

@dataclass
class WordSlot:
    """API 回傳嘅一個 ``words[]`` 項目（**字詞級**，唔係真·逐字）。"""

    text: str
    begin_ms: int
    end_ms: int
    punctuation: str = ""


@dataclass
class AsrSentence:
    """一個 ``sentences[]`` 項目。"""

    begin_ms: int
    end_ms: int
    text: str
    words: list[WordSlot] = field(default_factory=list)


@dataclass
class CharSlot:
    """最小渲染單位：一個單字 + 佢自己的 ``[begin_ms, end_ms)``。"""

    text: str
    begin_ms: int
    end_ms: int


@dataclass
class WordGroup:
    """一個詞 + 佢底下嘅 CharSlot。對應 timings.json 的 ``words[]``。

    ``estimated=True`` 表示**詞內部嘅逐字邊界係按比例平分估出嚟**（ADR-011 / 名詞表 C-4），
    唔係 ASR 真實畀嘅。日後字幕編輯 UI 要靠呢個 flag 話畀用戶知「呢啲要人手對」。
    """

    chars: list[CharSlot]

    #: True  = 詞內持有估算出來的字邊界（多字漢字詞被按比例平分）
    #: False = 詞本身嘅時間軸直接來自 API（單字詞、或純拉丁詞整體保留）
    estimated: bool = False

    #: 呢個詞後面跟住嘅標點（ADR-013：由 CharSlot.text 搬出嚟，CharSlot 永遠純字）
    punctuation: str = ""

    #: 呢個詞係唔係 ASR sentence 嘅最尾一個詞
    #: （ADR-013：當「隱含句末標點」做斷行點，防 ASR 漏畀標點）
    sentence_end: bool = False

    @property
    def begin_ms(self) -> int:
        return self.chars[0].begin_ms

    @property
    def end_ms(self) -> int:
        return self.chars[-1].end_ms

    @property
    def text(self) -> str:
        return "".join(c.text for c in self.chars)


@dataclass
class LyricLine:
    """一條字幕行（= 一條 ASS Dialogue event）。"""

    start_ms: int
    end_ms: int
    words: list[WordGroup] = field(default_factory=list)

    #: 呢行係點斷出嚟（ADR-013）：punctuation / sentence / max_chars / gap；"" = 未知
    break_reason: str = ""

    @property
    def text(self) -> str:
        """**純字**拼接（唔含標點；要顯示用嘅文字請用 :meth:`display_text`）。"""
        return "".join(w.text for w in self.words)

    def display_text(self, strip_punctuation: bool = True) -> str:
        """顯示用文字（ADR-013 統一規則）。

        - ``strip_punctuation=True``（預設）：行尾標點剝走；行中標點變**半形空格**
          （防雙空格：下一詞已以空白開頭就唔補）
        - ``strip_punctuation=False``：原標點照出（``--keep-punctuation``）
        """
        parts: list[str] = []
        n = len(self.words)
        for i, word in enumerate(self.words):
            parts.append(word.text)
            if not word.punctuation:
                continue
            if not strip_punctuation:
                parts.append(word.punctuation)
            elif i + 1 < n and not self.words[i + 1].text.startswith(" "):
                parts.append(" ")
        return "".join(parts)

    def iter_chars(self) -> Iterable[CharSlot]:
        """順序吐出本行所有 CharSlot（跨 WordGroup）。"""
        for word in self.words:
            yield from word.chars


@dataclass
class StyleConfig:
    """字幕樣式。ADR-012：內嵌落 timings.json，令 render 唔使外部參數都 self-contained。"""

    play_res_x: int = 1920
    play_res_y: int = 1080
    font_name: str = "Microsoft JhengHei"
    font_size: int = 48
    primary_color: str = "#FFFF00"        # 唱過（高亮）
    secondary_color: str = "#FFFFFF"      # 未唱（底色）
    outline_color: str = "#000000"
    alignment: int = 2                    # 底部置中
    margin_v: int = 0                     # 0 = 由 render 按 PlayResY 推算
    outline_width: float = 0.0            # 0 = 由 render 按 PlayResY 推算

    #: 雙行交替（ADR-027）：奇數句固定上行、偶數句固定下行，每行隔兩句才換。
    #: False（預設）= 舊行為，一句一個 event，完全唔會變。
    dual_line: bool = False
    #: 雙行「換行延遲」（ms）。有兩種語義，由 ``lead_in_ms`` 決定：
    #:   - ``lead_in_ms == 0``（舊行為）：下一句「開唱」之後幾耐才換行（ADR-027）
    #:   - ``lead_in_ms > 0``（新行為，ADR-031）：**句尾停留** —— 最後一個字唱完後
    #:     畫面仲留幾耐才清空；會夾住「同排下一句開唱」確保不疊字
    switch_delay_ms: int = 1000
    #: 歌詞提前顯示（ms，ADR-031）：每句最多提早幾耐出現。
    #: 長間奏期間唔會再提早幾十秒顯示下一句；0 = 完全關閉（回到 ADR-027 舊行為，
    #: 輸出 byte-identical）。
    lead_in_ms: int = 3000
    #: 倒數圓點（ADR-031）：長空白段進入歌詞前，喺歌詞上方逐點亮起 3 個「●」。
    #: 只在 lead_in_ms ≥ 1500 且該句確實有一段空白等待時才會出現。
    countdown_dots: bool = True
    #: 圓點出現門檻（ms，ADR-031 補記）：上一句結束後，同下一句開唱至少隔幾耐
    #: 才值得出倒數圓點。0 = 只要有一段完整空白就出。預設 5000（5 秒）。
    dot_min_gap_ms: int = 5000
    #: 雙行行距（px，上行底部同下行底部的距離）；0 = 按 font_size × 1.45 推算
    line_gap: int = 0
    #: 整體時間平移（ms，ADR-029）：正值 = 字幕整體延後。用於換了另一版剪輯的 MV
    #: （前奏長度唔同）。**唔會改 timings 的原始時間**，只喺 render 時偏移。
    shift_ms: int = 0
    #: 雙行水平錯位（% 畫面闊度，ADR-030）：負 = 偏左、正 = 偏右。0 = 居中。
    up_offset_pct: int = 0
    down_offset_pct: int = 0

    def resolved(self) -> "StyleConfig":
        """把 0 值換算成相對 PlayResY 的預設值。"""
        margin_v = self.margin_v or max(24, round(self.play_res_y * 0.055))
        outline = self.outline_width or max(2.0, round(self.play_res_y / 360, 1))
        gap = self.line_gap or max(24, round(self.font_size * 1.45))
        return replace(self, margin_v=margin_v, outline_width=outline, line_gap=gap)


# ---------------------------------------------------------------------------
# 外部工具偵測
# ---------------------------------------------------------------------------

def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in (
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ):
        if os.path.isfile(path):
            return path
    LOGGER.error(
        "搵唔到 ffmpeg。請先安裝系統層級 FFmpeg：\n"
        "  Windows ：winget install Gyan.FFmpeg\n"
        "  macOS   ：brew install ffmpeg\n"
        "  Linux   ：sudo apt install ffmpeg"
    )
    raise ToolMissingError("搵唔到 ffmpeg —— 安裝方法見 log（winget install Gyan.FFmpeg）")


def find_ffprobe() -> str:
    sibling = Path(find_ffmpeg()).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("ffprobe")
    if found:
        return found
    LOGGER.error("搵唔到 ffprobe（通常同 ffmpeg 一齊安裝）。")
    raise ToolMissingError("搵唔到 ffprobe（通常同 ffmpeg 一齊安裝）")


def probe_video_size(ffprobe: str, video: Path) -> tuple[int, int]:
    """讀取原片寬高。ADR-006：PlayResX/PlayResY 必須由原片讀出，唔准寫死。"""
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.match(r"^(\d+)x(\d+)$", (proc.stdout or "").strip())
    if not match:
        LOGGER.warning("ffprobe 讀唔到解像度，退回 1920x1080")
        return 1920, 1080
    return int(match.group(1)), int(match.group(2))


def probe_audio_codec(ffprobe: str, video: Path) -> str | None:
    """讀取音軌 codec，決定 ``-c:a copy`` 定係降級 AAC（ADR-009）。"""
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "").strip() or None


def probe_duration_ms(ffprobe: str, media: Path) -> int | None:
    """讀取媒體總時長（ms）；讀唔到就 ``None``。

    用途（ADR-020）：用戶自備嘅「純音樂 MV」若果係另一個剪輯版本，
    字幕會整體漂移 —— 燒錄前用時長比對做第一道預警。

    Args:
        ffprobe: ffprobe 可執行檔。
        media: 任何 ffmpeg 讀得到的影音檔。

    Returns:
        時長毫秒；解析失敗 → ``None``（唔好當 0，否則會被誤判為「差好遠」）。
    """
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(media)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return int(round(float((proc.stdout or "").strip()) * 1000))
    except ValueError:
        return None


def probe_has_video(ffprobe: str, media: Path) -> bool:
    """是否有視訊軌。

    純音樂 MV 必須有畫面（燒字幕要畫面）；揀到 mp3/wav 就係用戶搞錯，
    要在對話框即時擋住，唔好等到 ffmpeg 跑一半才失敗。
    """
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(media)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return bool((proc.stdout or "").strip())


def read_playres_from_ass(ass_path: Path) -> tuple[int, int] | None:
    """由 ASS 嘅 ``[Script Info]`` 讀 PlayResX/PlayResY。

    ADR-012：burn 淨係會收 .ass，所以要自己喺 ass 入面搵座標系，
    令 burn 唔使依賴 timings.json 都可以行得通。
    """
    x = y = None
    for line in ass_path.read_text(encoding="utf-8", errors="replace").splitlines():
        low = line.strip().lower()
        if low.startswith("playresx:"):
            x = int(re.sub(r"\D", "", line.split(":", 1)[1]) or 0)
        elif low.startswith("playresy:"):
            y = int(re.sub(r"\D", "", line.split(":", 1)[1]) or 0)
        if x and y:
            return x, y
    return None


# ---------------------------------------------------------------------------
# Stage 1 · 音訊抽取（TempAudio）
# ---------------------------------------------------------------------------

def extract_temp_audio(ffmpeg: str, video: Path, workdir: Path) -> Path:
    out = workdir / "temp_audio_16k.wav"
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(video), "-vn",
             "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        # ffmpeg 路徑存在但執行不到（或 PATH 解析失敗）→ 語意化成 ToolMissingError，
        # 唔好流一隻裸 FileNotFoundError 出去，否則 CLI 會出 traceback、UI 只顯示
        # "[WinError 2]"，兩種前端都要各自猜。
        LOGGER.error("ffmpeg 無法執行：%s", exc)
        raise ToolMissingError(f"ffmpeg 無法執行：{ffmpeg}") from exc
    if proc.returncode != 0 or not out.is_file():
        LOGGER.error("ffmpeg 抽音失敗：\n%s", (proc.stderr or "")[-1500:])
        raise AudioExtractionError(f"ffmpeg 抽音失敗（rc={proc.returncode}），詳見 log")
    LOGGER.info("TempAudio 完成：%.2f MB", out.stat().st_size / 1_048_576)
    return out


#: 全靜音門檻（int16 絕對值）。32768 滿刻度，32 ≈ −60 dBFS —— 數位靜音與
#: 「只有極底噪」都會命中，但正常錄音（即使很細聲）遠高於此。
SILENCE_PEAK_THRESHOLD = 32


def detect_silent_wav(path: str | Path, *,
                      threshold: int = SILENCE_PEAK_THRESHOLD) -> bool:
    """判斷 WAV 是否（接近）全靜音（審查報告 9.2-1）。

    用 stdlib ``wave`` 逐段掃樣本，**唔依賴 ffmpeg / numpy / pygame** ——
    所以純 CLI 環境（只有 ffmpeg）同無 ffmpeg 的 CI 都叫得，決定性 100%。

    為何唔用 ``ui.audio.extract_peaks``：pipeline 根本唔會 import UI 層
    （CLI 冇 pygame），把警告掛在 peaks 上等於 CLI 用戶永遠見唔到。

    Args:
        path: 16-bit PCM WAV（ffmpeg 抽音輸出就是 ``pcm_s16le``）。
        threshold: int16 絕對值門檻；最大樣本絕對值 < 門檻即當靜音。

    Returns:
        True 表示全段（接近）無聲；檔案有聲或格式唔支援時 False。

    Raises:
        OSError: 檔案讀唔到（權限／不存在）。
        wave.Error: 不是合法 WAV。
    """
    with wave.open(str(path), "rb") as f:
        width = f.getsampwidth()
        if width not in (1, 2):
            # 24/32-bit WAV 極少見（ffmpeg 一定出 s16le），保守當「有聲」
            LOGGER.debug("detect_silent_wav：sampwidth=%d 未支援，視為有聲", width)
            return False
        # 每次讀 4 秒，一小時音訊都只佔幾十 KB 峰值記憶體
        chunk_frames = 16_000 * 4
        while True:
            raw = f.readframes(chunk_frames)
            if not raw:
                return True
            if width == 1:
                samples = array.array("B", raw)
                peak = max((abs(v - 128) for v in samples), default=0)
                limit = threshold / 256      # 8-bit 門檻按比例縮小
            else:
                samples = array.array("h")
                samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
                peak = max((abs(v) for v in samples), default=0)
                limit = threshold
            if peak >= limit:
                return False


# ---------------------------------------------------------------------------
# Stage 2 · 人聲分離（可選，ADR-010 懶安裝）
# ---------------------------------------------------------------------------

def separate_vocals(wav: Path, workdir: Path) -> Path:
    """AI 人聲分離。**只影響送去做 ASR 嘅音源**，輸出影片必須保留原聲軌。"""
    import importlib.util

    if importlib.util.find_spec("audio_separator") is None:
        LOGGER.error(
            "未偵測到 audio-separator。請先執行：\n"
            "    pip install audio-separator\n"
            "（人聲分離會額外安裝 onnxruntime / torch，約需 500MB～2GB 磁碟空間）\n"
            "（唔用人聲分離可用預設模式，唔加 --separate-vocals）"
        )
        raise ToolMissingError(
            "未安裝 audio-separator —— 安裝方法見 log（pip install audio-separator）")

    LOGGER.info("進行人聲分離（首次執行需下載模型，可能要好幾分鐘）…")
    out_dir = workdir / "separated"
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        from audio_separator.separator import Separator  # type: ignore

        sep = Separator(output_dir=str(out_dir), output_format="wav")
        sep.load_model()
        outputs = sep.separate(str(wav))
        files = [str(x) for x in outputs if x] if isinstance(outputs, (list, tuple)) else [str(outputs)]
        vocal = next((f for f in files if "vocal" in os.path.basename(f).lower()), files[0])
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("人聲分離失敗（audio-separator 版本相容性問題）：%s", exc)
        raise AudioExtractionError(f"人聲分離失敗：{exc}") from exc

    ffmpeg = find_ffmpeg()
    normalized = workdir / "vocals_16k.wav"
    proc = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", vocal,
         "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(normalized)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        LOGGER.error("人聲標準化失敗：\n%s", (proc.stderr or "")[-1500:])
        raise AudioExtractionError(f"人聲標準化失敗（rc={proc.returncode}），詳見 log")
    LOGGER.info("人聲分離完成：%s", normalized.name)
    return normalized


# ---------------------------------------------------------------------------
# Stage 3 · 本地音頻上傳（ADR-003）
# ---------------------------------------------------------------------------

def _upload_file_with_progress(wav: Path, progress_cb: Callable[[float], None]) -> dict[str, Any]:
    """自己砌 multipart POST，邊傳邊報位元組進度（ADR-028）。

    ⚠️ 為何唔用 ``dashscope.Files.upload``：官方 SDK 喺 ``_upload_form()`` 一次
    ``f.read()`` 讀完整個檔才交畀 transport，中間**冇任何回調點** —— 想有真進度
    只能自己發請求。契約同 legacy SDK 完全一致：
    ``POST {base_http_api_url}/files``、``Authorization: Bearer``、
    multipart 欄位名 ``files``、回應 ``output.uploaded_files[0].file_id``。

    Args:
        wav: 要上傳的音訊檔。
        progress_cb: ``(pct)``，0–100；最多回調約 200 次。

    Returns:
        伺服器回應 JSON。

    Raises:
        Exception: 任何網絡／HTTP 錯誤（呼叫方會 fallback 去 SDK）。
    """
    import uuid

    import requests

    import dashscope

    size = wav.stat().st_size
    boundary = "----karaoke" + uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="files"; filename="{wav.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    total = len(head) + size + len(tail)
    step = max(1, total // 200)
    sent = 0
    last_reported = 0

    def _tick() -> None:
        nonlocal last_reported
        if sent - last_reported < step and sent < total:
            return
        last_reported = sent
        progress_cb(min(100.0, 100.0 * sent / total if total else 0.0))

    def _gen():
        nonlocal sent
        yield head
        sent += len(head)
        with wav.open("rb") as fh:
            for chunk in iter(lambda: fh.read(256 * 1024), b""):
                sent += len(chunk)
                _tick()
                yield chunk
        sent += len(tail)
        _tick()
        yield tail

    base = (getattr(dashscope, "base_http_api_url", "") or
            "https://dashscope.aliyuncs.com/api/v1").rstrip("/")
    resp = requests.post(
        f"{base}/files",
        data=_gen(),
        headers={
            "Authorization": f"Bearer {dashscope.api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(total),
        },
        timeout=900,
    )
    resp.raise_for_status()
    return resp.json()


def upload_audio(wav: Path,
                 progress_cb: Callable[[float], None] | None = None) -> str:
    """上傳 TempAudio → UploadedURL。

    主路線：``Files.upload()`` 拎 ``file_id`` → ``Files.get()`` 拎**預簽名 https URL**。
    ⚠️ Files.upload 淨係畀 file_id，**唔係**可以直接餵畀 ASR 的 URL。
    後備：``OssUtils.upload()`` 返 ``oss://`` URL（需配合 resolve header）。

    ADR-028：有 ``progress_cb`` 時改用自建 multipart 上傳（可報真進度）；
    自建路線失敗會**靜靜退回** SDK，唔會令整個 generate 失敗。
    """
    import dashscope

    LOGGER.info("上傳音訊到百煉臨時儲存 …")
    if progress_cb is not None:
        try:
            payload = _upload_file_with_progress(wav, progress_cb)
            file_id = payload["output"]["uploaded_files"][0]["file_id"]
            url = dashscope.Files.get(file_id=file_id).output.get("url")
            if not url:
                raise KeyError("Files.get() 冇返 url 字段")
            if url.startswith("http://"):
                url = url.replace("http://", "https://", 1)
            LOGGER.info("UploadedURL 取得成功（自建上傳 + 進度）")
            return url
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("自建上傳失敗（%s），退回 SDK 上傳（無進度）", exc)

    try:
        resp = dashscope.Files.upload(file_path=str(wav), purpose="assistants")
        file_id = resp.output["uploaded_files"][0]["file_id"]
        url = dashscope.Files.get(file_id=file_id).output.get("url")
        if not url:
            raise KeyError("Files.get() 冇返 url 字段")
        if url.startswith("http://"):
            url = url.replace("http://", "https://", 1)
        LOGGER.info("UploadedURL 取得成功")
        return url
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("預簽名 URL 路線失敗（%s），改用 oss:// + resolve header 後備路線", exc)

    from dashscope.utils.oss_utils import OssUtils

    oss_url, _ = OssUtils.upload(model=DEFAULT_ASR_MODEL, file_path=str(wav),
                                 api_key=dashscope.api_key)
    return oss_url


# ---------------------------------------------------------------------------
# Stage 4 · ASR 異步任務（ADR-001 / ADR-002 / ADR-004）
# ---------------------------------------------------------------------------

#: ADR-001：``yue`` 唔係粵語開關，係「粵語→普通話」開關，必須重映射做 zh
LANGUAGE_REMAP: dict[str, str] = {"yue": "zh"}
BANNED_ASR_MODELS = {"paraformer-v2", "paraformer"}   # ADR-004：假時間軸


def resolve_language_hints(language: str | None) -> tuple[list[str] | None, str | None]:
    if not language:
        return None, None
    raw = language.strip()
    mapped = LANGUAGE_REMAP.get(raw.lower(), raw)
    if mapped != raw:
        return [mapped], (
            f"已將 --language={raw} 重映射為 language_hints={[mapped]!r}"
            f"（見 ADR-001：yue 會觸發粵語→普通話轉寫）"
        )
    return [mapped], None


def _raise_if_cancelled(cancel_event: threading.Event | None, *,
                        stage: str = "") -> None:
    """取消檢查點：``cancel_event`` 已設即拋 :class:`TaskCancelled`。

    Args:
        cancel_event: UI 傳入的旗標；``None`` = 唔支援取消（CLI 舊行為）。
        stage: 當前階段名（只入 log，方便判斷取消發生在哪一步）。

    Raises:
        TaskCancelled: 用戶已按下取消。
    """
    if cancel_event is not None and cancel_event.is_set():
        LOGGER.info("收到取消訊號（階段：%s），中止任務", stage or "未知")
        raise TaskCancelled("任務已取消")


def _output_get(output: Any, key: str) -> Any:
    """讀 dashscope 回應欄位（屬性與 mapping 兩種寫法都收）。

    dashscope 的 ``output`` 是 DictMixin，真實回應兩種存取都通；但單元測試的
    fake 通常只給 dict。以前提交路徑用 ``resp.output.task_id``（屬性）、輪詢
    路徑卻用 ``out.get(...)``（mapping），同一個欄位兩套寫法 —— 統一在這裡。
    """
    if isinstance(output, dict):
        return output.get(key)
    return getattr(output, key, None)


def submit_and_wait_transcription(
    model: str,
    uploaded_url: str,
    language_hints: list[str] | None,
    poll_interval_s: int = 3,
    max_wait_s: int = 1800,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """提交 Filetrans 異步任務並輪詢至完成，回傳完整結果 JSON。

    ADR-002：**只傳** ``model`` / ``file_urls`` / ``language_hints``。
    ``enable_words`` / ``enable_itn`` 喺本 model 唔存在或無意義，禁止傳。

    Args:
        model: ASR 模型名。
        uploaded_url: 已上傳音訊的 URL（``oss://`` 會加 resolve header）。
        language_hints: 語言提示；``None`` = 自動偵測。
        poll_interval_s: 輪詢間隔（秒）。
        max_wait_s: 最長等待（秒）。
        cancel_event: 取消旗標；每圈輪詢**開頭**檢查，命中即拋
            :class:`TaskCancelled`（ADR-025）。

    Returns:
        ASR 完整結果 JSON。

    Raises:
        UsageError: model 已禁用。
        AsrError: 提交／輪詢／下載失敗，或輪詢逾時。
        TaskCancelled: 用戶取消。
    """
    if model in BANNED_ASR_MODELS:
        LOGGER.error(
            "model %r 已永久禁用（ADR-004）：佢嘅「100% 逐字」係線性插值假時間軸。"
            "請用 %s", model, DEFAULT_ASR_MODEL
        )
        raise UsageError(f"model {model!r} 已禁用（假時間軸，見 ADR-004）；請用 {DEFAULT_ASR_MODEL}")

    _raise_if_cancelled(cancel_event, stage="ASR 提交前")

    import requests
    from dashscope.audio.asr import Transcription

    kwargs: dict[str, Any] = {}
    if language_hints:
        kwargs["language_hints"] = language_hints
    if uploaded_url.startswith("oss://"):
        kwargs["headers"] = {"X-DashScope-OssResourceResolve": "enable"}

    LOGGER.info("提交 ASR 異步任務（model=%s, hints=%s）…", model, language_hints or "自動偵測")
    resp = Transcription.async_call(model=model, file_urls=[uploaded_url], **kwargs)
    if resp.status_code != 200:
        LOGGER.error("ASR 任務提交失敗（HTTP %s）：%s", resp.status_code,
                     json.dumps(getattr(resp, "output", {}), ensure_ascii=False, default=str)[:1200])
        raise AsrError(f"ASR 任務提交失敗（HTTP {resp.status_code}），詳見 log")

    task_id = _output_get(resp.output, "task_id")
    if not task_id:
        LOGGER.error("ASR 提交回應冇 task_id：%s",
                     json.dumps(getattr(resp, "output", {}), ensure_ascii=False, default=str)[:800])
        raise AsrError("ASR 提交回應冇 task_id，詳見 log")
    LOGGER.info("task_id = %s，開始輪詢 …", task_id)

    started = time.time()
    last_status = ""
    while True:
        # 取消檢查放在**每一圈的最頂**：fetch 可能 itself 慢幾秒，
        # 先檢查就保證「按取消後唔會再等一個 poll 間隔」。
        _raise_if_cancelled(cancel_event, stage="ASR 輪詢")
        fetched = Transcription.fetch(task=task_id)
        out = fetched.output or {}
        status = out.get("task_status", "")
        if status != last_status:
            LOGGER.info("  task_status = %s（已等 %.0fs）", status, time.time() - started)
            last_status = status
        if status == "SUCCEEDED":
            break
        if status == "FAILED":
            LOGGER.error("ASR 任務失敗：code=%s message=%s", out.get("code"), out.get("message"))
            raise AsrError(f"ASR 任務失敗：{out.get('message') or out.get('code') or '未知原因'}")
        if time.time() - started > max_wait_s:
            LOGGER.error("ASR 輪詢逾時（>%ds），task_id=%s", max_wait_s, task_id)
            raise AsrError(f"ASR 輪詢逾時（超過 {max_wait_s}s），task_id={task_id}")
        time.sleep(poll_interval_s)

    # ⚠️ 必須逐筆檢查 subtask_status，淨係 check task_status 會漏錯
    results = fetched.output.get("results") or []        # 複數！唔好寫 output.result
    ok_items = [r for r in results if r.get("subtask_status") == "SUCCEEDED"]
    if not ok_items:
        LOGGER.error("沒有任何 subtask 成功：%s",
                     json.dumps(results, ensure_ascii=False, default=str)[:1200])
        raise AsrError(f"沒有任何 subtask 成功（共 {len(results)} 筆），詳見 log")

    _raise_if_cancelled(cancel_event, stage="ASR 下載")
    data = requests.get(ok_items[0]["transcription_url"], timeout=120).json()
    LOGGER.info("識別完成，耗時 %.0fs", time.time() - started)
    return data


# ---------------------------------------------------------------------------
# Stage 5 · 解析 ASR JSON
# ---------------------------------------------------------------------------

def parse_asr_result(data: dict[str, Any]) -> list[AsrSentence]:
    transcripts = data.get("transcripts") or []
    if not transcripts:
        LOGGER.error("ASR 結果冇 transcripts 字段：%s", json.dumps(data, ensure_ascii=False)[:800])
        raise NoSubtitlesError("ASR 結果冇 transcripts 字段（等於冇識別到歌詞），詳見 log")

    sentences: list[AsrSentence] = []
    skipped = 0
    for raw in transcripts[0].get("sentences") or []:
        words: list[WordSlot] = []
        for w in raw.get("words") or []:
            text = (w.get("text") or "").strip()
            if not text:
                continue  # API 偶爾會出空白 token
            words.append(WordSlot(
                text=text,
                begin_ms=int(w.get("begin_time") or 0),
                end_ms=int(w.get("end_time") or 0),
                punctuation=(w.get("punctuation") or "").strip(),
            ))
        text = (raw.get("text") or "").strip()
        # 實測：ASR 會喺片尾吐出單字雜訊 token（例如 "用。"）→ 丟棄少於 2 個有效字嘅殘句
        if len(_PUNCT_RE.sub("", text)) < 2:
            skipped += 1
            LOGGER.debug("丟棄單字殘句：%r", text)
            continue
        sentences.append(AsrSentence(
            begin_ms=int(raw.get("begin_time") or 0),
            end_ms=int(raw.get("end_time") or 0),
            text=text,
            words=words,
        ))

    if skipped:
        LOGGER.info("已丟棄 %d 條單字雜訊殘句", skipped)
    LOGGER.info("解析完成：%d 句 / %d 個 WordSlot",
                len(sentences), sum(len(s.words) for s in sentences))
    return sentences


# ---------------------------------------------------------------------------
# Stage 6 · 拆字 + 組成 WordGroup（名詞表 C-4 / ADR-012）
# ---------------------------------------------------------------------------

_LATIN_RE = re.compile(r"^[A-Za-z0-9'’\-]+$")


def word_slot_to_group(word: WordSlot) -> WordGroup:
    """把一個 WordSlot 變成 WordGroup（內含 CharSlot）。

    - 單字或**純拉丁詞**（``hello`` / ``world``）→ 整體保留唔拆，`estimated=False`
    - 多字漢字詞 → **按比例平分**，`estimated=True`（呢個 flag 係字幕編輯 UI 嘅重要提示）
    - 標點**唔再黐落 CharSlot.text**，獨立存入 ``WordGroup.punctuation``（ADR-013）
    """
    text = word.text
    if len(text) <= 1 or _LATIN_RE.match(text):
        return WordGroup(chars=[CharSlot(text, word.begin_ms, word.end_ms)],
                         estimated=False, punctuation=word.punctuation)

    total = word.end_ms - word.begin_ms
    count = len(text)
    chars: list[CharSlot] = []
    cursor = word.begin_ms
    for i, ch in enumerate(text):
        next_ms = word.begin_ms + round(total * (i + 1) / count)
        if next_ms <= cursor:
            next_ms = cursor + 1
        chars.append(CharSlot(ch, cursor, next_ms))
        cursor = next_ms
    return WordGroup(chars=chars, estimated=True, punctuation=word.punctuation)


def build_word_groups(sentences: Iterable[AsrSentence]) -> list[WordGroup]:
    groups: list[WordGroup] = []
    for sentence in sentences:
        s_groups = [word_slot_to_group(w) for w in sentence.words]
        if s_groups:
            # ADR-013：sentence 最尾一個詞標 sentence_end，斷句時當「隱含句末標點」
            s_groups[-1] = replace(s_groups[-1], sentence_end=True)
        groups.extend(s_groups)
    LOGGER.info("拆字完成：%d 個 WordGroup（其中 %d 個含估算時間軸）/ %d 個 CharSlot",
                len(groups), sum(1 for g in groups if g.estimated),
                sum(len(g.chars) for g in groups))
    return groups


# ---------------------------------------------------------------------------
# Stage 7 · 正規化（OpenCC s2hk，ADR-008 押後到此步；ADR-012 有 CJK 先跑）
# ---------------------------------------------------------------------------

def normalize_groups_opencc(groups: list[WordGroup],
                            config: str = DEFAULT_OPENCC_CONFIG) -> list[WordGroup]:
    """對**整條 flattened 文字**做 OpenCC 轉換，再按原長度切分返。

    點解要成條 string 轉而唔係逐個字轉：OpenCC 用最長匹配處理詞組
    （例如 ``干什么→幹什麼`` 靠詞組判斷），逐字轉會錯。
    """
    from opencc import OpenCC

    original = "".join(c.text for g in groups for c in g.chars)
    if not has_cjk(original):
        LOGGER.info("偵測到純非中日韓內容（例如英語歌），略過 OpenCC（ADR-012）")
        return groups

    converter = OpenCC(config)
    converted = converter.convert(original)
    if len(converted) != len(original):
        LOGGER.warning("OpenCC(%s) 改變咗字數（%d → %d），已棄用轉換結果以保持時間軸正確",
                       config, len(original), len(converted))
        return groups

    out: list[WordGroup] = []
    cursor = 0
    for group in groups:
        new_chars: list[CharSlot] = []
        for c in group.chars:
            new_chars.append(CharSlot(converted[cursor:cursor + len(c.text)], c.begin_ms, c.end_ms))
            cursor += len(c.text)
        # ADR-013：標點而家喺 WordGroup 上，逐串轉換；字數一變就保留原樣（保命，
        # 主流標點簡繁相同，實務上唔會轉到）
        new_punct = group.punctuation
        if new_punct:
            p = converter.convert(new_punct)
            if len(p) == len(new_punct):
                new_punct = p
        out.append(replace(group, chars=new_chars, punctuation=new_punct))
    LOGGER.info("OpenCC(%s) 正規化完成", config)
    return out


# ---------------------------------------------------------------------------
# Stage 8 · 斷句（ADR-012：喺 **WordGroup 邊界** 切，唔會切穿一個詞）
# ---------------------------------------------------------------------------

def _line_reason(groups: list[WordGroup]) -> str:
    """呢行係由咩斷出嚟（ADR-013）：行尾詞帶標點 → punctuation；sentence 尾 → sentence。"""
    last = groups[-1]
    if last.punctuation:
        return "punctuation"
    if last.sentence_end:
        return "sentence"
    return ""   # 全歌最尾冇標點又唔係 sentence 尾嘅退化情況


def split_into_lines(
    groups: list[WordGroup],
    max_chars: int = 18,
    gap_threshold_ms: int = 800,
    hold_ms: int = 300,
    min_clause_chars: int = MIN_CLAUSE_CHARS,
    min_clause_ms: int = MIN_CLAUSE_MS,
    merge_max_chars: int = MERGE_MAX_CHARS,
) -> list[LyricLine]:
    """標點優先斷句（ADR-013，計劃書 §5；門檻 2026-10-29 修訂）。

    次序：
      1. 切分句（clause）：逢 WordGroup 帶 ``punctuation`` 或 ``sentence_end`` 就收結
      2. 定向合併太短 clause（**雙條件**：字數 ≤ ``min_clause_chars`` 且
         自身時長 < ``min_clause_ms``）：句末（hard）收尾 → **向後併**；
         短句要向前併但**合併後超 ``merge_max_chars``** → 放棄、照出短行
      3. 兜底：合併後仍超 ``max_chars`` 嘅 clause → 喺 WordGroup 邊界切，
         優先 ``gap > gap_threshold_ms`` 嘅位，記 ``max_chars`` / ``gap``
      4. 產生 ``LyricLine``，行尾留 ``hold_ms``（同下一行 begin 對齊）

    ``max_chars`` / ``gap`` 喺標點優先之下只係**安全網**；``merge_max_chars``（16）
    淨係約束「合併」 —— 合併係為咗消滅短行，唔係為咗砌長行。
    """
    # ---- Step 1：切分句（clause）----
    clauses: list[list[WordGroup]] = []
    current: list[WordGroup] = []
    for group in groups:
        current.append(group)
        if group.punctuation or group.sentence_end:
            clauses.append(current)
            current = []
    if current:
        clauses.append(current)   # 全歌最尾冇標點嘅尾巴（罕見）

    def _chars(gs: list[WordGroup]) -> int:
        # ADR-013：只數 CharSlot（純字）；標點同空格都唔計
        return sum(len(g.chars) for g in gs)

    def _is_short(gs: list[WordGroup]) -> bool:
        return (_chars(gs) <= min_clause_chars
                and (gs[-1].end_ms - gs[0].begin_ms) < min_clause_ms)

    def _kind(gs: list[WordGroup]) -> str:
        # 句末（hard）定句讀（soft）。
        # ⚠️ word 自帶標點**優先**過 sentence_end：逗號收尾（話未講完）就算撞正
        #    sentence 邊界都係 soft（向前併），sentence_end 只係「冇標點」時嘅
        #    fallback（B2：隱含句末標點）。實測《每當變幻時》「逝去了的，都已逝去」
        #    就係撞正呢個 case，向後併會拆散個 hook。
        punct = gs[-1].punctuation
        if punct:
            return "hard" if any(ch in _PUNCT_HARD for ch in punct) else "soft"
        if gs[-1].sentence_end:
            return "hard"
        return "soft"

    # ---- Step 2：定向合併太短嘅分句 ----
    merged: list[list[WordGroup]] = []
    cur: list[WordGroup] = []
    force_forward = False   # cur 太短，下一個 clause 必須併埋（向前）
    for clause in clauses:
        if not cur:
            cur = list(clause)
        elif force_forward:
            if _chars(cur) + _chars(clause) <= merge_max_chars:
                cur.extend(clause)          # 向前併（短句帶住下一句）
            else:
                merged.append(cur)          # 併咗會超 merge_max_chars → 放棄，短行照出
                cur = list(clause)
        elif (_is_short(clause) and _kind(clause) == "hard"
              and _chars(cur) + _chars(clause) <= merge_max_chars):
            cur.extend(clause)              # 句末收尾 → 向後併（R3：合併後唔可以超 merge_max_chars）
        else:
            merged.append(cur)
            cur = list(clause)
        # 統一複檢：cur 仍然太短就強制同下一個 clause 併埋
        # （R2 連鎖合併嘅終止條件：併到過門檻、或已經冇下一個 clause 為止）
        force_forward = _is_short(cur)
    if cur:
        merged.append(cur)

    # ---- Step 3 + 4：兜底切長句 → LyricLine ----
    sliced: list[tuple[list[WordGroup], str]] = []
    for clause in merged:
        if _chars(clause) <= max_chars:
            sliced.append((clause, _line_reason(clause)))
            continue
        piece: list[WordGroup] = []
        piece_len = 0
        for group in clause:
            if piece:
                gap = group.begin_ms - piece[-1].end_ms
                if piece_len + len(group.chars) > max_chars:
                    sliced.append((piece, "max_chars"))
                    piece, piece_len = [], 0
                elif gap > gap_threshold_ms:
                    sliced.append((piece, "gap"))
                    piece, piece_len = [], 0
            piece.append(group)
            piece_len += len(group.chars)
        if piece:
            sliced.append((piece, _line_reason(piece)))

    result: list[LyricLine] = []
    for i, (group_slice, reason) in enumerate(sliced):
        start = group_slice[0].begin_ms
        natural_end = group_slice[-1].end_ms + hold_ms
        if i + 1 < len(sliced):
            natural_end = min(natural_end, sliced[i + 1][0][0].begin_ms - 10)
        result.append(LyricLine(start_ms=start, end_ms=max(start + 1, natural_end),
                                words=group_slice, break_reason=reason))
    reasons = {r: sum(1 for _, x in sliced if x == r) for r in
               ("punctuation", "sentence", "max_chars", "gap") if any(x == r for _, x in sliced)}
    LOGGER.info("斷句完成：%d 行（標點優先 ADR-013；max_chars=%d, gap>%dms, 短句<=%d字/%dms, 合併上限%d字）"
                "斷點來源 %s",
                len(result), max_chars, gap_threshold_ms, min_clause_chars, min_clause_ms,
                merge_max_chars, reasons or "{}")
    return result


# ---------------------------------------------------------------------------
# Stage 9 · timings.json（ADR-012 canonical interchange format）
# ---------------------------------------------------------------------------

def lines_to_timings_doc(
    lines: list[LyricLine],
    style: StyleConfig,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """LyricLine[] → timings.json 內容（兩層 words[]→chars[] + self-contained style）。

    ``meta``（= doc ``source``）由呼叫方組裝；schema v3 起字幕編輯器 UI 儲存時會
    額外帶 ``edited_at``（ISO 時間）/ ``edited_by``（"ui"），generate 產生嘅新檔冇呢兩個字段。
    """
    return {
        "schema_version": TIMINGS_SCHEMA_VERSION,
        "source": meta,
        "style": {
            "play_res_x": style.play_res_x,
            "play_res_y": style.play_res_y,
            "font_name": style.font_name,
            "font_size": style.font_size,
            "primary_color": style.primary_color,
            "secondary_color": style.secondary_color,
            "outline_color": style.outline_color,
            "alignment": style.alignment,
            "margin_v": style.margin_v,
            "outline_width": style.outline_width,
            # ADR-027：只在啟用雙行時才寫，單行輸出維持同舊版 byte-identical
            **({"dual_line": True,
                "switch_delay_ms": style.switch_delay_ms,
                "line_gap": style.line_gap,
                # ADR-030：上下行水平錯位
                "up_offset_pct": style.up_offset_pct,
                "down_offset_pct": style.down_offset_pct} if style.dual_line else
               {"switch_delay_ms": style.switch_delay_ms}
               if style.switch_delay_ms != 1000 else {}),
            # ADR-031：提前顯示 / 倒數圓點（非預設值才寫，維持舊檔 byte-identical）
            **({"lead_in_ms": style.lead_in_ms} if style.lead_in_ms != 3000 else {}),
            **({"countdown_dots": False} if not style.countdown_dots else {}),
            **({"dot_min_gap_ms": style.dot_min_gap_ms}
               if style.dot_min_gap_ms != 5000 else {}),
            # ADR-029：平移值有非 0 才寫
            **({"shift_ms": style.shift_ms} if style.shift_ms else {}),
        },
        "lines": [
            {
                "start_ms": line.start_ms,
                "end_ms": line.end_ms,
                "text": line.display_text(),          # ADR-013：顯示用文字（已剝標點、含合併空格）
                # ADR-013：punctuation/sentence/max_chars/gap；schema v3 起 UI 手動斷句 = "manual"
                "break_reason": line.break_reason,
                "words": [
                    {
                        "text": word.text,            # 純字（唔含標點）
                        "begin_ms": word.begin_ms,
                        "end_ms": word.end_ms,
                        "estimated": word.estimated,
                        "punctuation": word.punctuation,   # ADR-013
                        "chars": [
                            {"text": c.text, "begin_ms": c.begin_ms, "end_ms": c.end_ms}
                            for c in word.chars
                        ],
                    }
                    for word in line.words
                ],
            }
            for line in lines
        ],
    }


def timings_doc_to_lines(doc: dict[str, Any]) -> tuple[list[LyricLine], StyleConfig]:
    """timings.json → (LyricLine[], StyleConfig)。**直接使用 JSON 內嘅絕對時間，唔重新推算**。

    ADR-013：``schema_version=1`` 舊檔自動相容 —— 標點黐喺 ``text`` 尾，讀入時自動
    拆返出嚟塞入 ``WordGroup.punctuation``（只 warn 唔 fail）。
    v2 相容（schema v3 起）：v2 已有獨立 punctuation 字段，讀入邏輯同 v3 完全一致，
    淨係 warn 提示舊版本；``break_reason`` 多出嘅 ``"manual"`` 值（UI 手動斷句）
    會原樣讀入，ASS 渲染唔受影響。
    """
    version = doc.get("schema_version")
    if version != TIMINGS_SCHEMA_VERSION:
        if version in (None, 1):
            note = "已自動做 v1 標點相容處理"
        elif version == 2:
            note = "v2 舊檔，讀入相容（見 schema v3 說明）"
        else:
            note = "可能會唔相容"
        LOGGER.warning("timings.json schema_version=%r（本程式係 %d），%s",
                       version, TIMINGS_SCHEMA_VERSION, note)
    is_v1 = version in (None, 1)   # v1：標點黐喺 text / chars[-1] 尾

    # ⚠️ 防禦性處理：JSON 可能係人手改過、或者由壞狀態產生（實測：舊版 generate 寫過 null 顏色）
    #    一律用 `or <預設值>` 兜底，並且喺顏色值唔合法時畀清晰錯誤，
    #    而唔係等到 build_karaoke_ass 先爆 AttributeError
    defaults = StyleConfig()
    raw_style = doc.get("style") or {}
    style = StyleConfig(
        play_res_x=int(raw_style.get("play_res_x") or defaults.play_res_x),
        play_res_y=int(raw_style.get("play_res_y") or defaults.play_res_y),
        font_name=raw_style.get("font_name") or defaults.font_name,
        font_size=int(raw_style.get("font_size") or defaults.font_size),
        primary_color=raw_style.get("primary_color") or defaults.primary_color,
        secondary_color=raw_style.get("secondary_color") or defaults.secondary_color,
        outline_color=raw_style.get("outline_color") or defaults.outline_color,
        alignment=int(raw_style.get("alignment") or defaults.alignment),
        margin_v=int(raw_style.get("margin_v") or 0),
        outline_width=float(raw_style.get("outline_width") or 0.0),
        # ADR-027：舊檔冇呢三個欄位 → 自動當單行，行為同以前一模一樣
        dual_line=bool(raw_style.get("dual_line", False)),
        switch_delay_ms=int(raw_style.get("switch_delay_ms") or defaults.switch_delay_ms),
        line_gap=int(raw_style.get("line_gap") or 0),
        shift_ms=int(raw_style.get("shift_ms") or 0),
        up_offset_pct=int(raw_style.get("up_offset_pct") or 0),
        down_offset_pct=int(raw_style.get("down_offset_pct") or 0),
        # ADR-031：舊檔冇 lead_in_ms → 用新預設 3 秒；明寫 0 才是「關閉提前顯示」
        lead_in_ms=(defaults.lead_in_ms if raw_style.get("lead_in_ms") is None
                    else max(0, int(raw_style.get("lead_in_ms") or 0))),
        countdown_dots=bool(raw_style.get("countdown_dots", True)),
        dot_min_gap_ms=(defaults.dot_min_gap_ms
                        if raw_style.get("dot_min_gap_ms") is None
                        else max(0, int(raw_style.get("dot_min_gap_ms") or 0))),
    )
    for name, value in (("primary_color", style.primary_color),
                        ("secondary_color", style.secondary_color),
                        ("outline_color", style.outline_color)):
        try:
            hex_to_ass_color(value)
        except ValueError as exc:
            LOGGER.error("timings.json 的 style.%s 有問題：%s", name, exc)
            raise UsageError(f"timings.json 的 style.{name} 有問題：{exc}") from exc

    lines: list[LyricLine] = []
    for raw_line in doc.get("lines") or []:
        words: list[WordGroup] = []
        for raw_word in raw_line.get("words") or []:
            # ADR-013：v1 舊檔嘅標點黐喺 text 尾 → 自動拆出嚟；v2 有獨立 punctuation 字段
            raw_text = str(raw_word.get("text", ""))
            punct = raw_word.get("punctuation")
            if punct is None:
                m = _TRAILING_PUNCT_RE.search(raw_text)
                # ⚠️ 淨係剝「前面仲有字」嘅尾部標點；全串都係標點嘅怪 word 就照原樣保留
                punct = m.group(0) if (m and m.start() > 0) else ""
                if punct:
                    raw_text = raw_text[:m.start()]
            chars = [
                CharSlot(text=c.get("text", ""),
                         begin_ms=int(c.get("begin_ms", 0)),
                         end_ms=int(c.get("end_ms", 0)))
                for c in raw_word.get("chars") or []
            ]
            if punct and chars:
                # v1 嘅標點黐喺最後一個 CharSlot 尾（多字詞只會喺尾字）
                last = chars[-1]
                if last.text.endswith(punct) and len(last.text) > len(punct):
                    chars[-1] = CharSlot(last.text[: -len(punct)], last.begin_ms, last.end_ms)
            if chars:
                words.append(WordGroup(chars=chars, estimated=bool(raw_word.get("estimated", False)),
                                       punctuation=punct))
        if words:
            lines.append(LyricLine(
                start_ms=int(raw_line.get("start_ms", words[0].begin_ms)),
                end_ms=int(raw_line.get("end_ms", words[-1].end_ms)),
                words=words,
                break_reason=str(raw_line.get("break_reason", "")),
            ))
    LOGGER.info("由 timings.json 讀入 %d 行", len(lines))
    return lines, style


# ---------------------------------------------------------------------------
# Stage 10 · 生成 ASS（ADR-005 / C-5 / C-6）
# ---------------------------------------------------------------------------

def escape_ass_text(text: str) -> str:
    """把歌詞原文轉成 ASS Dialogue 的安全文字（ADR-024 §2.7）。

    三個處理，**次序不可以調換**：

    1. ``\\`` → ``\\\\``（反斜線雙寫）
    2. ``\\n`` → ``\\N``（ASS 硬換行）
    3. ``{`` ``}`` → ``\\{`` ``\\}``（override block 界定符）

    為何次序關鍵：反斜線必須**最先**跳脫，否則第 2 步插入的 ``\\N`` 會被第 1 步
    二次跳脫成字面文字，用戶會見到反斜線；大括號放最後，因為它插入的新反斜線
    不應該再被跳脫。

    ⚠️ **實測限制（2026-11-03，真 libass 燒錄量測）**：反斜線雙寫**唔能**阻止
    ``\\N`` 換行 —— ``AB\\\\NCD`` 仍然渲染成兩行（libass 由第二個 ``\\`` 起照樣
    解讀 ``\\N``）。所以這一步只保證「唔會形成 override tag」，唔保證阻止轉義序列。
    真正徹底的做法要在輸入層改寫文字（例如插入零寬空格），但會污染
    ``timings.json`` 的文字內容，令搜尋／編輯對不上 —— 不值得，故此保留雙寫。
    另外，override tag 本來就只在大括號內生效，所以**大括號才是真正的注入點**，
    第 3 步才是最關鍵的一步。

    Args:
        text: 原始歌詞（可含換行、反斜線、大括號）。

    Returns:
        可直接嵌入 ASS Dialogue Text 欄位的字串。
    """
    # 1) 先跳脫原始反斜線（次序關鍵，見 docstring）
    out = text.replace("\\", "\\\\")
    # 2) 換行 → ASS 硬換行（先處理 CRLF，否則一個換行會變兩個 \N）
    out = out.replace("\r\n", "\\N").replace("\r", "\\N").replace("\n", "\\N")
    # 3) 最後跳脫大括號 —— 這才是真正擋 override tag 的一步
    return out.replace("{", "\\{").replace("}", "\\}")


def _shift_lines(lines: list[LyricLine], delta_ms: int) -> list[LyricLine]:
    """整體平移所有時間（ADR-029）。

    **只影響 render 出來的 .ass**，`timings.json` 的原始時間完全不變 ——
    這樣換不同剪輯版本的 MV 時可以微調，又唔會破壞 canonical 資料。

    Args:
        lines: 原始歌詞行。
        delta_ms: 位移（毫秒）；正值 = 全部延後，負值 = 提早。

    Returns:
        平移後的新 list（不修改輸入）。
    """
    if not delta_ms:
        return lines
    out: list[LyricLine] = []
    for line in lines:
        words: list[WordGroup] = []
        for word in line.words:
            chars = [replace(c, begin_ms=max(0, c.begin_ms + delta_ms),
                             end_ms=max(0, c.end_ms + delta_ms)) for c in word.chars]
            words.append(replace(word, chars=chars))
        out.append(replace(line, start_ms=max(0, line.start_ms + delta_ms),
                           end_ms=max(0, line.end_ms + delta_ms), words=words))
    return out


def _karaoke_pieces(line: LyricLine, gap_ms: int, strip_punctuation: bool) -> list[str]:
    """把一行歌詞砌成 ASS 逐字片段（單行／雙行共用）。

    C-5 停頓補償：前一字 end 與本字 begin 相差 > ``gap_ms`` 會插一條無文字的
    ``{\\k<gap>}``，令高亮等埋真實停頓。ADR-013 的標點處理亦喺呢度做。
    """
    pieces: list[str] = []
    prev_end: int | None = None
    n_words = len(line.words)
    for wi, word in enumerate(line.words):
        for c in word.chars:
            if prev_end is not None:
                gap = c.begin_ms - prev_end
                if gap > gap_ms:
                    pieces.append("{\\k%d}" % ms_to_cs(gap))
            pieces.append("{\\kf%d}%s" % (ms_to_cs(c.end_ms - c.begin_ms), escape_ass_text(c.text)))
            prev_end = c.end_ms
        # ADR-013：標點喺 WordGroup 邊界處理（行尾剝走 / 行中變空格 + 停頓）
        if word.punctuation:
            if wi + 1 < n_words:
                nxt = line.words[wi + 1]
                if not strip_punctuation:
                    pieces.append(escape_ass_text(word.punctuation))
                elif not nxt.text.startswith(" "):
                    # 防雙空格：英文 word 自帶前導空格時唔補空格，
                    # 停頓交返俾下一字嘅 C-5 機制
                    gap = nxt.begin_ms - word.end_ms
                    if gap > gap_ms:
                        pieces.append("{\\k%d} " % ms_to_cs(gap))
                        # ⚠️ 食咗呢段 gap（R6）：唔更新 prev_end 嘅話，
                        # 下一個字會再計一次同一段 gap、插多一條 \k（停頓雙倍）
                        prev_end = nxt.begin_ms
                    else:
                        pieces.append(" ")
            elif not strip_punctuation:
                # --keep-punctuation：行尾標點照出（同 v1 行為一致）
                pieces.append(escape_ass_text(word.punctuation))
    return pieces


def compute_display_windows(starts: Sequence[int], ends: Sequence[int], *,
                            lead_in_ms: int, dual_line: bool,
                            delay_ms: int = 1000) -> list[tuple[int, int]]:
    """計算每句喺畫面上嘅顯示窗口 ``(start_ms, end_ms)`` —— **唯一真源**。

    `main.build_karaoke_ass`（燒錄）同 `ui.widgets.overlay`（預覽）都呼叫呢個
    函數，所以預覽同成品永遠一致（ADR-027 嘅「所見非所得」警告）。

    兩種模式：

    **舊行為（``lead_in_ms == 0``）** — 與 ADR-027 完全相同，輸出 byte-identical：
      - 單行：窗口 = 句子本身
      - 雙行：第 1、2 句由 0 開始；第 ``i`` 句喺「前一句開唱 + ``delay_ms``」出現，
        到「下一句開唱 + ``delay_ms``」消失

    **新行為（``lead_in_ms > 0``）** — ADR-031：
      - 每句最多提早 ``lead_in_ms`` 出現，長間奏唔會提早幾十秒顯示下一句
      - 同排（雙行 = 奇偶分排；單行 = 同一行）上一句未清空前，本句唔會出現
      - 句尾停留 ``delay_ms``（「換行延遲」新語義），但**唔可以蓋過下一句開唱**

    Args:
        starts: 每句開唱時間（ms）。
        ends: 每句結束時間（ms，已含句尾 hold）。
        lead_in_ms: 提前顯示上限；0 = 舊行為。
        dual_line: 是否雙行交替。
        delay_ms: 換行延遲 / 句尾停留（ms）。

    Returns:
        與輸入等長的 ``(start_ms, end_ms)`` list，保證 ``end_ms > start_ms``。
    """
    n = len(starts)
    if n == 0:
        return []
    if lead_in_ms <= 0:
        out: list[tuple[int, int]] = []
        for i in range(n):
            if not dual_line:
                out.append((starts[i], ends[i]))
                continue
            start = 0 if i < 2 else starts[i - 1] + delay_ms
            end = starts[i + 1] + delay_ms if i + 1 < n else ends[i]
            # 快歌句距不足 delay 時，寧願本句遲啲出，都唔好同排兩個 event 疊字
            if end <= start:
                start = min(start, starts[i])
                end = max(ends[i], start + 500)
            out.append((start, end))
        return out

    lead = max(0, int(lead_in_ms))
    hold = max(0, int(delay_ms))
    out = []
    for i in range(n):
        start = max(0, starts[i] - lead)
        end = ends[i] + hold
        prev_same_row = i - 2 if dual_line else i - 1
        if prev_same_row >= 0:
            # 上一句仲喺畫面（包括句尾停留）→ 本句要等佢清空
            start = max(start, out[prev_same_row][1])
        next_same_row = i + 2 if dual_line else i + 1
        if next_same_row < n:
            # 句尾停留唔可以拖到下一句已經開唱（會同下一句搶同一行）
            end = min(end, starts[next_same_row])
        if end <= start:
            end = start + 300
        out.append((start, end))
    return out


def compute_countdown_dots(starts: Sequence[int], windows: Sequence[tuple[int, int]], *,
                           lead_in_ms: int, ends: Sequence[int] | None = None,
                           min_gap_ms: int = 5000, dot_ms: int = 500,
                           dot_count: int = 3) -> list[list[int]]:
    """每句嘅倒數圓點出現時間（ADR-031）。

    兩個條件**同時**成立才出圓點：

    1. **空白夠長**：上一句結束（``ends[i-1]``，第一句當 0）到本句開唱，至少
       ``min_gap_ms``（預設 5000）。太短的句距就算有少少空白都唔出 —— 否則間奏
       兩三秒的歌會滿屏圓點。
    2. **真的有空白位**：本句嘅顯示窗起點要早過 ``start - dot_ms * dot_count``，
       即圓點唔會壓住上一句。

    Args:
        starts: 每句開唱時間（ms）。
        windows: :func:`compute_display_windows` 嘅結果。
        lead_in_ms: 提前顯示上限（0 = 功能關閉）。
        ends: 每句結束時間（ms）；唔傳就當句與句之間冇空隙（只有第一句可能出）。
        min_gap_ms: 空白門檻（ms）；0 = 唔設門檻。
        dot_ms: 每點間隔（ms）。
        dot_count: 圓點數目。

    Returns:
        每句一個 list：``[t1, t2, t3]``（圓點逐個亮起嘅時間）；冇圓點 = ``[]``。
    """
    out: list[list[int]] = []
    if lead_in_ms <= 0:
        return [[] for _ in starts]
    # 3 點 × 0.5s = 1.5s：第一點喺開唱前 1.5s，最後一點喺開唱前 0.5s 亮起，
    # 一直亮到開唱一刻（所以整段佔用 lead 的最後 1.5 秒）
    span = dot_ms * max(1, dot_count)
    for i, start in enumerate(starts):
        prev_end = ends[i - 1] if (ends is not None and i > 0) else 0
        gap = start - prev_end
        first = start - span
        if gap < max(0, min_gap_ms) or windows[i][0] > first:
            out.append([])
        else:
            out.append([first + k * dot_ms for k in range(dot_count)])
    return out


def build_karaoke_ass(lines: list[LyricLine], style: StyleConfig, gap_ms: int = 50,
                      strip_punctuation: bool = True) -> str:
    """產生完整 ASS 文字（KaraokeAss）。

    ADR-013 標點處理（喺 **WordGroup 邊界**做，唔係逐字）：
      - ``strip_punctuation=True``（預設）：行尾標點**剝走**；行中標點變**半形空格**，
        並喺空格位插 ``{\\k<gap_cs>}`` 令高亮停一停（食住兩句之間嘅真實停頓）；
        下一詞已以空白開頭（英文）就唔補，避免雙空格
      - ``strip_punctuation=False``（``--keep-punctuation``）：標點原樣照出
        （輸出同 v1 完全一致 —— 標點黐喺前一個字嘅 syllable 度）

    ADR-031：顯示窗由 :func:`compute_display_windows` 計（提前顯示上限 + 句尾停留），
    並按 :func:`compute_countdown_dots` 喺開唱前 1.5 秒逐個亮起 3 粒圓點
    （Layer 1，唔會同層 0 的歌詞打架）。
    """
    s = style.resolved()
    if s.shift_ms:
        lines = _shift_lines(lines, s.shift_ms)
        LOGGER.info("整體時間平移 %+d ms（只影響本次 render，timings.json 不變）", s.shift_ms)
    header = [
        "[Script Info]",
        "; Generated by karaoke-ass-generator",
        "; 編輯來源：timings.json（canonical）。改時間請改 json 再 render，唔好直接改呢個檔",
        f"; schema_version: {TIMINGS_SCHEMA_VERSION}",
        "ScriptType: v4.00+",
        f"PlayResX: {s.play_res_x}",          # ADR-006：由 ffprobe 讀原片，唔准寫死
        f"PlayResY: {s.play_res_y}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: None",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Karaoke,"
        f"{s.font_name},{s.font_size},"
        f"{hex_to_ass_color(s.primary_color)},"      # 唱過（高亮）
        f"{hex_to_ass_color(s.secondary_color)},"    # 未唱（底色）
        f"{hex_to_ass_color(s.outline_color)},"
        "&H80000000,"
        "0,0,0,0,100,100,0,0,"
        f"1,{s.outline_width},0,"
        f"{s.alignment},20,20,{s.margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events: list[str] = []
    dual = bool(s.dual_line) and len(lines) > 0
    starts = [ln.start_ms for ln in lines]
    ends = [ln.end_ms for ln in lines]
    windows = compute_display_windows(starts, ends, lead_in_ms=s.lead_in_ms,
                                      dual_line=dual, delay_ms=s.switch_delay_ms)
    dot_times = (compute_countdown_dots(starts, windows, lead_in_ms=s.lead_in_ms,
                                        ends=ends, min_gap_ms=s.dot_min_gap_ms)
                 if s.countdown_dots else [[] for _ in lines])

    # ADR-030：垂直位置跟「對齊」設定（2 底 / 3 中下 / 8 頂 / 5 中）
    # \an 的值同時決定 y 係「底邊」(an2) 抑或「頂邊」(an8)／「中心」(an5)。
    an = s.alignment if s.alignment in (2, 5, 8) else 2
    if dual:
        if an == 8:
            up_y = s.margin_v
            down_y = s.margin_v + s.line_gap
        elif an == 5:
            mid = s.play_res_y // 2
            up_y = mid - s.line_gap // 2
            down_y = mid + s.line_gap // 2
        else:
            an = 2
            down_y = s.play_res_y - s.margin_v
            up_y = down_y - s.line_gap
        cx_mid = s.play_res_x // 2
        up_x = cx_mid + round(s.play_res_x * s.up_offset_pct / 100)
        down_x = cx_mid + round(s.play_res_x * s.down_offset_pct / 100)
    else:
        # 單行唔用 \pos（沿用 Style 的 Alignment + MarginV），但圓點要座標 → 計出嚟
        if an == 8:
            y = s.margin_v
        elif an == 5:
            y = s.play_res_y // 2
        else:
            y = s.play_res_y - s.margin_v

    dot_size = max(6, round(s.font_size / 3))
    # 3 粒「●」喺 fs/3 下剛好等於一個中文字嘅 advance（實測 ● 同 CJK 都係全寬），
    # 所以圓點事件用「3 點 + 其餘歌詞（隱形）」拼出來，總寬度同歌詞事件一模一樣 →
    # \an2 置中之後，第一粒點自動貼住第一個字，唔需要估字型度量（ADR-031 補記）。
    dot_fsp = max(0, round(s.font_size * 0.05))
    dot_lift = round(s.font_size * 0.80)     # 行上方：底邊再上移（實測 ~0.15 字級淨空）
    dot_drop = round(s.font_size * 0.70)     # 頂部對齊時：放行下方

    def _dot_anchor(row_y: int, row_an: int) -> int:
        """圓點事件 ``\\an2`` 的 y（= 該行文字框底邊）。

        放喺目標行上方；頂部對齊（``an8``）時上方冇位，改放行下方。
        """
        bottom = row_y + s.font_size if row_an == 8 else (
            row_y + s.font_size // 2 if row_an == 5 else row_y)
        return bottom - dot_lift if row_an != 8 else bottom + dot_drop

    for i, line in enumerate(lines):
        pieces = _karaoke_pieces(line, gap_ms, strip_punctuation)
        start_ms, end_ms = windows[i]
        chars = list(line.iter_chars())

        if not dual:
            row_an = an if an in (5, 8) else 2
            row_x, row_y = s.play_res_x // 2, y
            head = ""
        else:
            # 奇數句（索引 0,2,4…）固定上行、偶數句（1,3,5…）固定下行
            is_up = i % 2 == 0
            row_x, row_y = (up_x, up_y) if is_up else (down_x, down_y)
            row_an = an
            head = f"{{\\an{an}\\pos({row_x},{row_y})}}"

        # 預覽／提前顯示期：先用一條空 \k 佔住時間軸，否則 \kf 會提早啟動
        lead = ""
        if chars and chars[0].begin_ms > start_ms:
            lead = "{\\k%d}" % ms_to_cs(chars[0].begin_ms - start_ms)
        body = head + lead + "".join(pieces)

        events.append(
            f"Dialogue: 0,{ms_to_ass_time(start_ms)},{ms_to_ass_time(end_ms)},"   # ADR-005
            f"Karaoke,,0,0,0,,{body}")

        # --- 倒數圓點（ADR-031）：逐個亮起，最後一點亮到開唱一刻 ---
        # ⚠️ 用「開唱時間」starts[i] 而唔係顯示窗開始 start_ms —— 兩者喺提前顯示
        # 被上一句夾住時會唔同，用錯就會成段圓點被過濾走。
        lyric_start = starts[i]
        appears = [t for t in dot_times[i] if t < lyric_start]
        if not appears:
            continue
        lyric_text = line.display_text(strip_punctuation)[1:]   # 第一個字位讓畀圓點
        dy = _dot_anchor(row_y, row_an)
        prev_show: int | None = None
        for k, appear_ms in enumerate(appears):
            shown = max(0, appear_ms)          # 片頭：負時間夾到 0
            end_at = appears[k + 1] if k + 1 < len(appears) else lyric_start
            if end_at <= shown or (prev_show is not None and shown <= prev_show):
                continue
            prev_show = shown
            # 已亮 k+1 點 + 未亮（隱形）點 + 隱形歌詞尾巴 → 總寬度永遠等於原句，
            # 所以三條 event 嘅左邊界完全一致，第一粒點唔會隨點數左右跳。
            lit = "●" * (k + 1)
            dark = "●" * max(0, 3 - (k + 1))
            tail = (f"{{\\alpha&HFF&\\fsp0\\fs{s.font_size}}}"
                    f"{escape_ass_text(lyric_text)}" if lyric_text else "")
            events.append(
                f"Dialogue: 1,{ms_to_ass_time(shown)},{ms_to_ass_time(end_at)},"
                f"Karaoke,,0,0,0,,{{\\an2\\fs{dot_size}\\fsp{dot_fsp}"
                f"\\pos({row_x},{dy})}}{lit}{{\\alpha&HFF&}}{dark}{tail}")

    return "\n".join(header + events) + "\n"


# ---------------------------------------------------------------------------
# Stage 11 · ffmpeg 燒錄（ADR-006 / ADR-009）
# ---------------------------------------------------------------------------

#: ffmpeg filter 有特殊語義、唔可以出現喺 ass 檔名字元（用於燒錄時的臨時改名）
_FILTER_UNSAFE_RE = re.compile(r"[=:,'\"\[\];\\]")


def _rel_or_abs(path: Path, base: Path) -> str:
    """盡量相對化；跨磁碟區時退回絕對路徑（審查報告 §2.1 P0）。

    ``os.path.relpath`` 在 Windows 上當兩路徑位於不同磁碟區時會拋
    ``ValueError: path is on mount 'C:', start on mount 'D:'`` —— 而「MV 放
    D:\\、字幕輸出放 C:\\」是很常見的用戶配置，以前會直接 traceback。

    ffmpeg 的 ``-i`` 與**輸出檔參數**都接受絕對路徑；真正不可以放絕對路徑的
    只有 ``-vf ass=<...>`` 內容（冒號會被當 filter option 分隔符），而該處
    已用「``cwd`` + 純檔名」處理（ADR-006），所以 fallback 絕對路徑完全安全。

    Args:
        path: 要傳給 ffmpeg 的路徑。
        base: ffmpeg 的 ``cwd``。

    Returns:
        相對路徑；跨磁碟區時為絕對路徑。
    """
    try:
        return os.path.relpath(str(path), str(base))
    except ValueError:
        LOGGER.info("路徑與工作目錄不在同一磁碟區，改用絕對路徑：%s", path)
        return os.path.abspath(str(path))


def _mmss(ms: int) -> str:
    """毫秒 → ``m:ss``（進度文字用，唔需要百分秒）。"""
    sec = max(0, int(ms)) // 1000
    return f"{sec // 60}:{sec % 60:02d}"


def _read_ffmpeg_progress_us(path: str) -> int | None:
    """讀 ffmpeg ``-progress`` 臨時檔，回傳最新 ``out_time``（微秒）。

    ⚠️ ffmpeg 的 ``out_time_ms`` 其實係**微秒**（歷史遺留命名），同
    ``out_time_us`` 同值；兩個 key 都收，以檔案中最後一個為準。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    latest: int | None = None
    for raw in text.splitlines():
        key, _sep, value = raw.strip().partition("=")
        if key in ("out_time_us", "out_time_ms"):
            try:
                latest = int(value)
            except ValueError:
                continue
    return latest


def _run_ffmpeg(cmd: list[str], *, cwd: str,
                cancel_event: threading.Event | None,
                progress_cb: Callable[[int, int], None] | None = None,
                total_ms: int = 0) -> tuple[int, str]:
    """執行 ffmpeg，支援中途取消（ADR-025）同編碼進度（ADR-028）。

    為何唔用 ``subprocess.run``：它會一路阻塞到程序結束，中間冇任何檢查點，
    用戶只能等（燒錄長片可達數十分鐘）。有 ``cancel_event`` 時改用
    ``Popen`` + poll 迴圈，命中即 ``terminate()``，5 秒內唔死就 ``kill()``。

    ⚠️ stderr **同 ``-progress`` 都必須導向臨時檔**而非 ``PIPE``：poll 迴圈
    期間冇人同步讀 pipe，ffmpeg 寫滿 64KB 緩衝就會阻塞，變成「取消都取消
    唔到」的死鎖。``-progress`` 寫檔之後，迴圈每 0.2 秒讀一次即可。

    Args:
        cmd: 完整命令列。
        cwd: 工作目錄（ADR-006：ass 所在目錄）。
        cancel_event: 取消旗標；``None`` 走原本的阻塞式 ``run``。
        progress_cb: ``(done_ms, total_ms)``；只在有 ``cancel_event`` 時生效。
        total_ms: 影片總時長（毫秒），用嚟計百分比；0 = 唔報進度。

    Returns:
        ``(returncode, stderr)``。

    Raises:
        TaskCancelled: 用戶取消（ffmpeg 已被終止）。
    """
    if cancel_event is None:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return proc.returncode, proc.stderr or ""

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8",
                                errors="replace") as err_file:
        run_cmd = list(cmd)
        prog_path: str | None = None
        if progress_cb is not None and total_ms > 0:
            fd, prog_path = tempfile.mkstemp(prefix="karaoke_ffprog_", suffix=".txt")
            os.close(fd)
            # -progress / -nostats 係 global option，放喺 ffmpeg 之後最穩陣
            run_cmd = [run_cmd[0], "-progress", prog_path, "-nostats"] + run_cmd[1:]

        try:
            proc = subprocess.Popen(run_cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=err_file)
            last_step = -1
            while proc.poll() is None:
                if cancel_event.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        LOGGER.warning("ffmpeg 未能在 5 秒內結束，強制 kill")
                        proc.kill()
                        proc.wait(timeout=5)
                    LOGGER.info("已終止 ffmpeg（用戶取消燒錄）")
                    raise TaskCancelled("燒錄已取消")
                if prog_path:
                    us = _read_ffmpeg_progress_us(prog_path)
                    if us is not None:
                        done = max(0, min(total_ms, us // 1000))
                        step = done // 250          # 每 0.25 秒才更新一次 UI
                        if step != last_step:
                            last_step = step
                            progress_cb(done, total_ms)   # type: ignore[misc]
                time.sleep(0.2)
            if prog_path:
                progress_cb(total_ms, total_ms)           # type: ignore[misc]
            err_file.seek(0)
            return proc.returncode, err_file.read()
        finally:
            if prog_path:
                try:
                    os.remove(prog_path)
                except OSError:
                    pass


def burn_subtitles(
    ffmpeg: str,
    video: Path,
    ass_path: Path,
    output_path: Path,
    play_res_x: int,
    play_res_y: int,
    audio_codec: str | None,
    *,
    cancel_event: threading.Event | None = None,
    total_ms: int = 0,
    progress_cb: Callable[[int, int], None] | None = None,
) -> None:
    """把 ASS 燒錄進影片。

    ADR-006 鐵律：
      1. **永遠唔好**把路徑直接寫入 ``-vf ass=<path>``
         （Windows 嘅 ``C:`` 冒號會畀 ffmpeg 當 filter option 分隔符）。
         改為 ``cwd=<ass 所在目錄>`` + filter 只用**檔名**。
      2. ffmpeg 命令**必須**帶 ``original_size=WxH``。

    Args:
        ffmpeg: ffmpeg 執行檔路徑。
        video: 原片。
        ass_path: 字幕檔。
        output_path: 輸出 mp4。
        play_res_x: ASS 座標系寬（ADR-006）。
        play_res_y: ASS 座標系高。
        audio_codec: 原片音軌 codec，用於決定 ``-c:a copy`` 抑或降級 aac。
        cancel_event: 取消旗標（ADR-025）；命中時拋 ``TaskCancelled`` 並刪除
            半截輸出檔。

    Raises:
        BurnError: ffmpeg 回非零或輸出檔不存在（退出碼 5，ADR-024）。
        TaskCancelled: 用戶取消。
    """
    workdir = ass_path.parent
    ass_name = ass_path.name
    cleanup_target: Path | None = None
    if _FILTER_UNSAFE_RE.search(ass_name):
        safe_name = "_burn_tmp.ass"
        shutil.copyfile(ass_path, workdir / safe_name)
        ass_name = safe_name
        cleanup_target = workdir / safe_name
        LOGGER.info("檔名含 ffmpeg filter 特殊字元，已改用臨時檔名燒錄：%s", safe_name)

    try:
        base = [
            ffmpeg, "-y", "-v", "error",
            "-i", _rel_or_abs(video, workdir),
            "-vf", f"ass={ass_name}:original_size={play_res_x}x{play_res_y}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        ]
        # ADR-009：音軌優先用 copy，唔相容就降級 aac 192k
        if audio_codec in {"aac", "mp3", "ac3", "eac3", "alac"}:
            audio_args = ["-c:a", "copy"]
        elif audio_codec is None:
            audio_args = []
        else:
            LOGGER.warning("音軌 codec=%s 唔適合直接 copy 入 mp4，降級為 aac 192k", audio_codec)
            audio_args = ["-c:a", "aac", "-b:a", "192k"]

        out_arg = _rel_or_abs(output_path, workdir)
        try:
            LOGGER.info("開始燒錄字幕（libx264 crf18 medium）…")
            rc, err = _run_ffmpeg(base + audio_args + [out_arg], cwd=str(workdir),
                                  cancel_event=cancel_event,
                                  progress_cb=progress_cb, total_ms=total_ms)
            if rc != 0 or not output_path.is_file():
                LOGGER.warning("copy 音軌失敗，降級為 aac 192k 重試…\n%s", err[-600:])
                rc, err = _run_ffmpeg(base + ["-c:a", "aac", "-b:a", "192k", out_arg],
                                      cwd=str(workdir), cancel_event=cancel_event,
                                      progress_cb=progress_cb, total_ms=total_ms)
        except TaskCancelled:
            # 半截 mp4 會令用戶以為「燒錄成功但冇字幕」→ 一定要清走。
            # 但刪不到（例如被播放器鎖住）**唔可以蓋掉取消語義** —— 只記錄，
            # 照拋 TaskCancelled，否則 UI 會把「我按了取消」顯示成「燒錄失敗」。
            try:
                output_path.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning("取消後刪除半截輸出失敗（%s），請手動刪除：%s", exc, output_path)
            raise
        if rc != 0 or not output_path.is_file():
            LOGGER.error("ffmpeg 燒錄失敗（rc=%s）：\n%s", rc, err[-2500:])
            raise BurnError(f"ffmpeg 燒錄失敗（rc={rc}），詳見 log")
    finally:
        if cleanup_target and cleanup_target.is_file():
            cleanup_target.unlink(missing_ok=True)

    LOGGER.info("燒錄完成：%s（%.2f MB）", output_path.name, output_path.stat().st_size / 1_048_576)


# ---------------------------------------------------------------------------
# Subcommand: generate
# ---------------------------------------------------------------------------

def resolve_api_key(cli_key: str | None) -> str:
    if cli_key and cli_key.strip():
        return cli_key.strip()
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    env = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if env:
        return env
    LOGGER.error(
        "搵唔到 DashScope API Key。請用以下任一方式提供：\n"
        "  1) --api-key \"sk-xxxx\"\n"
        "  2) setx DASHSCOPE_API_KEY \"sk-xxxx\"  （PowerShell: $env:DASHSCOPE_API_KEY=\"sk-xxxx\"）\n"
        "  3) 喺執行目錄放 .env 檔，內容：DASHSCOPE_API_KEY=sk-xxxx"
    )
    raise UsageError("搵唔到 DashScope API Key —— 三種設定方法見 log")


# ---------------------------------------------------------------------------
# 純 API 層（字幕編輯器 UI 經 import main 呼叫；CLI cmd_* 係薄包裝）
# ---------------------------------------------------------------------------

#: 階段回呼：stage ∈ {"抽音","上傳","ASR","斷句","渲染","燒錄"}；message 係人讀進度說明。
#: UI 靠呢個做分階段進度燈（B4 決定），CLI 傳 None 完全不受影響。
ProgressCB = Callable[[str, str], None]


@dataclass
class GenerateOptions:
    """generate 全部參數（UI / CLI 共用；對應 CLI 旗標見 build_parser）。"""

    input: Path
    output_dir: Path | None = None
    model: str = DEFAULT_ASR_MODEL
    language: str | None = None
    api_key: str | None = None
    separate_vocals: bool = False
    max_chars: int = 18
    gap_threshold_ms: int = 800
    hold_ms: int = 300
    min_clause_chars: int = MIN_CLAUSE_CHARS
    min_clause_ms: int = MIN_CLAUSE_MS
    merge_max_chars: int = MERGE_MAX_CHARS
    keep_punctuation: bool = False
    save_raw: bool = False
    poll_interval_s: int = 3
    #: PlayRes 會由 generate_timings 按原片實測改寫（ADR-006），UI 唔使預先填
    style: StyleConfig = field(default_factory=StyleConfig)
    #: 取消旗標（ADR-025）。用 ``default_factory`` 而唔係 ``None``，令 UI 可以
    #: 無條件 ``opts.cancel_event.set()``；CLI 唔理它就等於唔支援取消。
    #: ⚠️ 因為 Event 唔可以 pickle，GenerateOptions 亦唔應該 deepcopy 到別的用途。
    cancel_event: threading.Event = field(default_factory=threading.Event)


@dataclass
class GenerateResult:
    """generate_timings() 回傳：UI 用嚟列產出清單 + 跳編輯器。"""

    json_path: Path
    ass_path: Path
    raw_asr_path: Path | None
    num_lines: int
    num_chars: int
    num_estimated_words: int


def generate_timings(opts: GenerateOptions, *,
                     progress_cb: ProgressCB | None = None,
                     percent_cb: Callable[[float], None] | None = None) -> GenerateResult:
    """影片 → 百煉 ASR → timings.json + ass（唔出影片）。

    行為同舊 cmd_generate 完全一致（log 逐句相同）；錯誤路徑改為拋
    :class:`KaraokeError` 子類（ADR-024），由 :func:`main` 轉回原本的 CLI 退出碼。
    UI 背景 thread 呼叫時只需 ``except main.KaraokeError``，唔再需要猜 ``SystemExit``。

    Args:
        opts: 全部 generate 參數。
        progress_cb: 階段回呼（可選），stage ∈ 抽音/上傳/ASR/斷句/渲染。
        percent_cb: 進度百分比回呼（0–100，ADR-028）；暫時只有上傳階段會用。

    Returns:
        GenerateResult：產出路徑 + 統計。

    Raises:
        UsageError: 輸入唔存在 / 缺 API key。
        ToolMissingError: 缺 ffmpeg / ffprobe / audio-separator。
        AudioExtractionError: 抽音或人聲分離失敗。
        AsrError: ASR 提交／輪詢／下載失敗。
        NoSubtitlesError: ASR 冇識別到內容，或斷句後零行。
        TaskCancelled: 用戶在任一階段取消（``opts.cancel_event``，ADR-025）。
    """
    video = opts.input
    if not video.is_file():
        LOGGER.error("輸入影片唔存在：%s", video)
        raise UsageError(f"輸入影片唔存在：{video}")

    cancel = opts.cancel_event
    _raise_if_cancelled(cancel, stage="開始")
    api_key = resolve_api_key(opts.api_key)
    output_dir = opts.output_dir or video.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()
    width, height = probe_video_size(ffprobe, video)
    LOGGER.info("影片 %dx%d", width, height)

    json_path = output_dir / f"{video.stem}_timings.json"
    ass_path = output_dir / f"{video.stem}_karaoke.ass"

    import dashscope

    dashscope.api_key = api_key

    workdir = Path(tempfile.mkdtemp(prefix="karaoke_"))
    try:
        if progress_cb:
            progress_cb("抽音", "抽取 16kHz 單聲道音訊 …")
        asr_source = extract_temp_audio(ffmpeg, video, workdir)
        # 9.2-1：全靜音 MV 唔 raise（可能係用戶故意測樣式），只警告 —— 因為
        # 後續 ASR 一定會回空結果，用戶至少知道「唔係程式壞，係條片冇聲」。
        try:
            if detect_silent_wav(asr_source):
                LOGGER.warning(
                    "偵測到音軌（接近）全靜音 —— ASR 很可能冇識別到內容。"
                    "請確認 MV 有聲軌（可用 --separate-vocals 試人聲分離）。")
        except (OSError, wave.Error) as exc:      # noqa: BLE001 — 偵測失敗唔應擋流程
            LOGGER.debug("全靜音偵測失敗（忽略）：%s", exc)
        _raise_if_cancelled(cancel, stage="抽音")
        if opts.separate_vocals:
            if progress_cb:
                progress_cb("抽音", "人聲分離（首次需下載模型）…")
            asr_source = separate_vocals(asr_source, workdir)
            _raise_if_cancelled(cancel, stage="人聲分離")

        if progress_cb:
            progress_cb("上傳", "上傳音訊到百煉臨時儲存 …")

        def _upload_pct(pct: float) -> None:
            if percent_cb is not None:
                percent_cb(pct)
            if progress_cb is not None:
                progress_cb("上傳", f"上傳音訊 … {pct:.0f}%")

        # 冇 percent_cb 時保持舊呼叫方式（單一位置參數）—— 外部／測試的
        # monkeypatch 都係 replace 一個食一個參數的函式，唔好破壞佢。
        if percent_cb is not None:
            uploaded_url = upload_audio(asr_source, progress_cb=_upload_pct)
        else:
            uploaded_url = upload_audio(asr_source)
        _raise_if_cancelled(cancel, stage="上傳")
        if progress_cb:
            progress_cb("上傳", "")       # ADR-028：空 message = 呢個階段完成

        hints, warn = resolve_language_hints(opts.language)
        if warn:
            LOGGER.warning(warn)
        if progress_cb:
            progress_cb("ASR", "提交異步轉寫任務並輪詢 …")
        raw = submit_and_wait_transcription(
            model=opts.model, uploaded_url=uploaded_url, language_hints=hints,
            poll_interval_s=opts.poll_interval_s, cancel_event=cancel,
        )
        raw_asr_path: Path | None = None
        if opts.save_raw:
            raw_asr_path = output_dir / f"{video.stem}_raw_asr.json"
            atomic_write_json(raw_asr_path, raw)
        if progress_cb:
            progress_cb("ASR", "")        # ADR-028：階段完成

        if progress_cb:
            progress_cb("斷句", "解析 ASR 結果、拆字、OpenCC、斷句 …")
        _raise_if_cancelled(cancel, stage="斷句")
        sentences = parse_asr_result(raw)
        groups = build_word_groups(sentences)
        groups = normalize_groups_opencc(groups)
        lines = split_into_lines(groups, max_chars=opts.max_chars,
                                 gap_threshold_ms=opts.gap_threshold_ms, hold_ms=opts.hold_ms,
                                 min_clause_chars=opts.min_clause_chars,
                                 min_clause_ms=opts.min_clause_ms,
                                 merge_max_chars=opts.merge_max_chars)
        if not lines:
            LOGGER.error("冇產生任何字幕行，可能係 ASR 冇識別到內容。")
            raise NoSubtitlesError("冇產生任何字幕行 —— ASR 可能冇識別到內容，詳見 log")
        if progress_cb:
            progress_cb("斷句", "")       # ADR-028：階段完成

        style = replace(opts.style, play_res_x=width, play_res_y=height)   # ADR-006
        if progress_cb:
            progress_cb("渲染", "寫出 timings.json + ASS …")
        transcripts = (raw.get("transcripts") or [{}])[0]
        meta = {
            "video": str(video),
            "video_width": width,
            "video_height": height,
            "model": opts.model,
            "language_hints": hints,
            "asr_content_duration_ms": transcripts.get("content_duration_in_milliseconds"),
            "opencc": DEFAULT_OPENCC_CONFIG if has_cjk(lines[0].text) else None,
            "max_chars": opts.max_chars,
            "gap_threshold_ms": opts.gap_threshold_ms,
            "hold_ms": opts.hold_ms,
            "min_clause_chars": opts.min_clause_chars,     # ADR-013
            "min_clause_ms": opts.min_clause_ms,           # ADR-013
            "merge_max_chars": opts.merge_max_chars,       # ADR-013 修訂
            "strip_punctuation": not opts.keep_punctuation,   # ADR-013
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        doc = lines_to_timings_doc(lines, style, meta)

        # 取消檢查放在**寫檔之前**：取消後唔應該留下「一半的產出」
        # （ass 有、json 冇，用戶下次開檔會以為失敗）。
        _raise_if_cancelled(cancel, stage="寫檔")
        atomic_write_json(json_path, doc)
        LOGGER.info("timings.json 已輸出：%s", json_path)

        ass_text = build_karaoke_ass(lines, style,
                                     strip_punctuation=not opts.keep_punctuation)
        atomic_write_text(ass_path, ass_text)
        LOGGER.info("ASS 字幕已輸出：%s", ass_path)
        num_chars = sum(len(list(ln.iter_chars())) for ln in lines)
        num_estimated = sum(1 for ln in lines for w in ln.words if w.estimated)
        LOGGER.info("  %d 行 / %d 個 CharSlot / 其中 %d 個係估算出嚟（estimated）",
                    len(lines), num_chars, num_estimated)
        LOGGER.info("  第一行：%s", lines[0].display_text())
    finally:
        if workdir.is_dir():
            shutil.rmtree(workdir, ignore_errors=True)

    LOGGER.info("完成 ✅（淨係出字幕，要出影片請再行 burn）")
    LOGGER.info("  timings : %s", json_path)
    LOGGER.info("  ass     : %s", ass_path)
    return GenerateResult(json_path=json_path, ass_path=ass_path, raw_asr_path=raw_asr_path,
                          num_lines=len(lines), num_chars=num_chars,
                          num_estimated_words=num_estimated)


def cmd_generate(args: argparse.Namespace) -> int:
    """CLI 薄包裝：argparse.Namespace → GenerateOptions → generate_timings()。"""
    # 顏色先驗：唔想用戶打錯色碼之後要等抽音／上傳完才見到 traceback
    _validate_style_colors(
        primary_color=args.primary_color,
        secondary_color=args.secondary_color,
        outline_color=args.outline_color,
    )
    generate_timings(GenerateOptions(
        input=Path(args.input).expanduser(),
        output_dir=Path(args.output_dir).expanduser() if args.output_dir else None,
        model=args.model,
        language=args.language,
        api_key=args.api_key,
        separate_vocals=args.separate_vocals,
        max_chars=args.max_chars,
        gap_threshold_ms=args.gap_threshold,
        hold_ms=args.hold_ms,
        min_clause_chars=args.min_clause_chars,
        min_clause_ms=args.min_clause_ms,
        merge_max_chars=args.merge_max_chars,
        keep_punctuation=args.keep_punctuation,
        save_raw=args.save_raw,
        poll_interval_s=args.poll_interval,
        # ⚠️ 之前漏咗呢行 —— CLI 嘅 --font-size / --primary-color 等全部被靜默忽略，
        #    生成出嚟永遠係 StyleConfig() 預設值（48px 黃字）。
        style=StyleConfig(
            font_name=args.font_name,
            font_size=args.font_size,
            primary_color=args.primary_color,
            secondary_color=args.secondary_color,
            outline_color=args.outline_color,
            alignment=args.alignment,
            margin_v=args.margin_v,
            outline_width=args.outline_width,
            # ADR-031：提前顯示 / 倒數圓點
            lead_in_ms=getattr(args, "lead_in_ms", 3000),
            countdown_dots=getattr(args, "countdown_dots", True),
            dot_min_gap_ms=getattr(args, "dot_min_gap_ms", 5000),
        ),
    ))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: render
# ---------------------------------------------------------------------------

def render_timings(doc: dict[str, Any], style: StyleConfig | None = None, *,
                   strip_punctuation: bool = True) -> str:
    """timings.json dict → ASS 文字（UI 入口）。

    Args:
        doc: timings.json 反序列化後嘅 dict。
        style: 覆寫樣式；None 就用 doc 內嵌 style。
        strip_punctuation: ADR-013 標點處理（UI 對應 doc ``source.strip_punctuation``）。

    Returns:
        完整 ASS 文字。
    """
    lines, doc_style = timings_doc_to_lines(doc)
    return build_karaoke_ass(lines, style or doc_style, strip_punctuation=strip_punctuation)


def cmd_render(args: argparse.Namespace) -> int:
    src = Path(args.timings).expanduser()
    if not src.is_file():
        LOGGER.error("timings.json 唔存在：%s", src)
        raise UsageError(f"timings.json 唔存在：{src}")

    doc = json.loads(src.read_text(encoding="utf-8"))
    lines, style = timings_doc_to_lines(doc)

    # 樣式覆寫（本次 render 生效；想持久落 JSON 要加 --sync-style）
    overrides = {k: v for k, v in (
        ("font_name", args.font_name), ("font_size", args.font_size),
        ("primary_color", args.primary_color), ("secondary_color", args.secondary_color),
        ("outline_color", args.outline_color), ("margin_v", args.margin_v),
        ("alignment", args.alignment), ("outline_width", args.outline_width),
        ("shift_ms", getattr(args, "shift_ms", None)),
        # ADR-031
        ("lead_in_ms", getattr(args, "lead_in_ms", None)),
        ("countdown_dots", getattr(args, "countdown_dots", None)),
        ("dot_min_gap_ms", getattr(args, "dot_min_gap_ms", None)),
    ) if v is not None}
    if overrides:
        # 顏色先驗：--sync-style 會把覆寫寫回 JSON，壞色碼唔可以入檔
        _validate_style_colors(
            **{k: v for k, v in overrides.items() if k.endswith("_color")})
        style = replace(style, **overrides)
        LOGGER.info("套用本次樣式覆寫：%s", ", ".join(overrides))

    if args.sync_style:
        raw_style = doc.setdefault("style", {})
        for key, value in overrides.items():
            raw_style[key] = value
        # --sync-style 會覆寫用戶的 canonical 檔 → 必須原子寫 + 留 .bak（§2.8）
        atomic_write_json(src, doc)
        LOGGER.info("已將樣式覆寫寫回 %s", src.name)

    if args.output:
        out = Path(args.output).expanduser()
    else:
        # 預設：把 "<name>_timings.json" 換成 "<name>_karaoke.ass"
        stem_name = src.name
        for suffix in ("_timings.json", ".json"):
            if stem_name.endswith(suffix):
                stem_name = stem_name[: -len(suffix)]
                break
        out = src.with_name(f"{stem_name}_karaoke.ass")

    atomic_write_text(out, build_karaoke_ass(lines, style,
                                            strip_punctuation=not args.keep_punctuation))
    LOGGER.info("render 完成：%s（%d 行）", out, len(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: burn
# ---------------------------------------------------------------------------

def burn_video(video: Path, ass_path: Path, output_path: Path, *,
               progress_cb: ProgressCB | None = None,
               percent_cb: Callable[[float], None] | None = None,
               cancel_event: threading.Event | None = None) -> Path:
    """影片 + .ass → 燒錄 mp4（UI 入口；CLI 見 cmd_burn）。

    ADR-012：座標系由 .ass 自己讀出，唔使依賴 timings.json。

    Args:
        video: 原始 MV 影片。
        ass_path: 要燒錄嘅 .ass 檔。
        output_path: 輸出 mp4 路徑。
        progress_cb: 階段回呼（stage = "燒錄"）。
        percent_cb: 編碼百分比回呼（0–100），供進度條用（ADR-028）。
        cancel_event: 取消旗標（ADR-025）；命中時 ffmpeg 會被終止，
            半截輸出檔會被刪除，並拋 :class:`TaskCancelled`。

    Returns:
        輸出 mp4 路徑。

    Raises:
        ToolMissingError: 缺 ffmpeg / ffprobe。
        BurnError: ffmpeg 燒錄失敗（退出碼 5，ADR-024）。
        TaskCancelled: 用戶取消。
    """
    ffmpeg = find_ffmpeg()
    ffprobe = find_ffprobe()

    # ADR-012：座標系由 .ass 自己讀出，burn 唔使依賴 timings.json
    playres = read_playres_from_ass(ass_path)
    if playres:
        width, height = playres
        LOGGER.info("由 ASS 讀到座標系 %dx%d", width, height)
    else:
        width, height = probe_video_size(ffprobe, video)
        LOGGER.warning("ASS 冇寫 PlayRes，改用原片解像度 %dx%d", width, height)

    audio_codec = probe_audio_codec(ffprobe, video)
    total_ms = probe_duration_ms(ffprobe, video) or 0
    if progress_cb:
        progress_cb("燒錄", "開始燒錄字幕（libx264 crf18 medium）…")

    def _tick(done: int, total: int) -> None:
        """ffmpeg 進度 → 百分比 + 人讀文字（ADR-028）。"""
        pct = (100.0 * done / total) if total else 0.0
        if percent_cb is not None:
            percent_cb(pct)
        if progress_cb is not None:
            progress_cb("燒錄", f"燒錄中 {_mmss(done)} / {_mmss(total)}（{pct:.0f}%）")

    burn_subtitles(ffmpeg=ffmpeg, video=video, ass_path=ass_path, output_path=output_path,
                   play_res_x=width, play_res_y=height, audio_codec=audio_codec,
                   cancel_event=cancel_event, total_ms=total_ms,
                   progress_cb=_tick if (progress_cb or percent_cb) else None)
    if progress_cb:
        progress_cb("燒錄", "完成")
    return output_path


def cmd_burn(args: argparse.Namespace) -> int:
    video = Path(args.input).expanduser()
    ass_path = Path(args.subtitle).expanduser()
    if not video.is_file():
        LOGGER.error("輸入影片唔存在：%s", video)
        raise UsageError(f"輸入影片唔存在：{video}")
    if not ass_path.is_file():
        LOGGER.error("字幕檔唔存在：%s", ass_path)
        raise UsageError(f"字幕檔唔存在：{ass_path}")

    output = Path(args.output).expanduser() if args.output else video.with_name(
        f"{video.stem}_karaoke.mp4")

    burn_video(video, ass_path, output)
    LOGGER.info("完成 ✅  影片：%s", output)
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_style_args(parser: argparse.ArgumentParser, *, overrides: bool = False) -> None:
    """加入字幕樣式參數。

    ``overrides=True`` （render 用）：參數是「覆寫」，**未提供時為 None**，
        令 render 可以分辨「使用者未指定」與「使用者要求改成預設值」。
    ``overrides=False``（generate 用）：參數有實質預設值，會寫入 timings.json。
    """
    suffix = "（覆寫 timings.json 內的設定）" if overrides else ""
    group = parser.add_argument_group("字幕樣式")
    if not overrides:
        group.add_argument(
            "--font-name", default="Microsoft JhengHei",
            help="字幕字型名稱，需為系統已安裝字型（預設 Microsoft JhengHei，"
                 "Windows 內建且中文字形完整）")
        group.add_argument(
            "--font-size", type=int, default=48,
            help="字幕字級，單位 px，以 ASS PlayResY 為基準（預設 48；1080p 建議 60–80，"
                 "歌詞較長時可降至 40–48）")
    else:
        group.add_argument("--font-name", help="字幕字型名稱" + suffix)
        group.add_argument("--font-size", type=int, help="字幕字級（px）" + suffix)
    group.add_argument(
        "--primary-color", default=None if overrides else "#FFFF00",
        help="「已唱」高亮色，十六進位 #RRGGBB（預設 #FFFF00 黃）。"
             "此色同時用於 UI 色塊與燒錄結果" + suffix)
    group.add_argument(
        "--secondary-color", default=None if overrides else "#FFFFFF",
        help="「未唱」底色，十六進位 #RRGGBB（預設 #FFFFFF 白）。"
             "卡拉OK 效果即由此色漸變到「已唱」色" + suffix)
    group.add_argument(
        "--outline-color", default=None if overrides else "#000000",
        help="字幕描邊色，十六進位 #RRGGBB（預設 #000000 黑）。"
             "淺色畫面建議改用深色描邊或加陰影以提升可讀性" + suffix)
    group.add_argument(
        "--margin-v", type=int, default=None if overrides else 0,
        help="字幕底部垂直留白，單位 px（預設 0 = 依 PlayResY 自動推算，1080p 約 59px）。"
             "MV 自帶內嵌字幕時建議調大至 120–200 以避免重疊" + suffix)
    if overrides:
        group.add_argument(
            "--shift-ms", type=int,
            help="整體時間平移，單位 ms（正值 = 字幕全部延後、負值 = 提早）。"
                 "用於換了另一版剪輯的 MV（前奏長度唔同）；只影響本次 render" + suffix)
    # UI 主頁「對齊」「描邊粗幼」對應項（ADR-015：主頁設定 = 燒錄權威）
    group.add_argument(
        "--alignment", type=int, choices=[2, 3, 5, 8],
        default=None if overrides else 2,
        help="字幕對齊位置（ASS Alignment 編號）：2 = 底部置中、3 = 中下、"
             "5 = 中部置中、8 = 頂部置中（預設 2）" + suffix)
    group.add_argument(
        "--outline-width", type=float, default=None if overrides else 0.0,
        # ⚠️ 「4%%」：argparse 會用 help 做 ``%`` 格式化，單一個 % 跟住全形括號
        #    會拋 ValueError: unsupported format character（實測 render -h 直接 crash）
        help="描邊粗細，單位 px（預設 0 = 依 PlayResY 自動推算，一般等於字級的 4%%）。"
             "畫面雜亂時可加粗至 3–4px" + suffix)
    # ADR-031：提前顯示 / 倒數圓點（UI 主頁「提前顯示」「倒數圓點」對應項）
    group.add_argument(
        "--lead-in-ms", type=int, default=None if overrides else 3000,
        help="歌詞提前顯示上限，單位 ms（預設 3000）。長間奏期間唔會再提早幾十秒"
             "出下一句；0 = 關閉（回到舊行為）" + suffix)
    group.add_argument(
        "--countdown-dots", action=argparse.BooleanOptionalAction,
        default=None if overrides else True,
        help="長空白段進入歌詞前，喺歌詞上方逐點亮起 3 個圓點（預設開啟；"
             "--no-countdown-dots 關閉）" + suffix)
    group.add_argument(
        "--dot-min-gap-ms", type=int, default=None if overrides else 5000,
        help="圓點門檻：上一句結束後至少隔幾多 ms 才出倒數圓點（預設 5000；"
             "0 = 只要有一段空白就出）" + suffix)


def _read_version() -> str:
    """讀取版本號（``--version`` 用）。

    CLI 刻意**唔** import ``ui.version`` —— 為讀一個版本號而拉入 UI 套件
    （連帶 ttkbootstrap）唔值得。此處與 ``ui/version.py`` 讀**同一個**
    ``VERSION`` 檔，所以版本號只有一處要改。

    Returns:
        版本字串，例如 ``"1.0.0"``；檔案缺失或為空時回傳 ``"dev"``（唔拋例外，
        版本號讀唔到不應該令 CLI 開唔到）。
    """
    try:
        text = (Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8")
    except OSError:
        return "dev"
    stripped = text.strip()
    first = stripped.splitlines()[0].strip() if stripped else ""
    return first or "dev"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="將本地 MV 轉為卡拉OK字幕：generate（產生 json 與 ass）→ 人工或 UI 校對 → "
                    "render → burn",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "典型流程：\n"
            "  1) python main.py generate -i \"MV.mp4\"        # 產生 *_timings.json 與 *_karaoke.ass\n"
            "  2) 以字幕編輯器開啟 *_timings.json，校對時間軸與錯字\n"
            "  3) python main.py render MV_timings.json        # 重新產生 ass（無須重跑 ASR）\n"
            "  4) python main.py burn -i \"MV.mp4\" -s MV_karaoke.ass\n"
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="顯示 DEBUG 層級日誌（等同於子命令前後加 -v）")
    # 版本號（來源：專案根目錄 VERSION，與 UI 標題同一個檔）
    # action="version" 會喺解析到呢個參數時即刻印出並結束，
    # 所以即使 subparsers 係 required=True，`main.py --version` 一樣有效。
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_read_version()}",
                        help="顯示版本號並結束")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- generate
    g = sub.add_parser("generate", help="影片 → ASR → timings.json 與 ass（**不輸出影片**）",
                       description="抽音、上傳、語音辨識、斷句、渲染一次完成；"
                                   "產出 *_timings.json（唯一真源）與 *_karaoke.ass。")
    g.add_argument("-v", "--verbose", action="store_true", help="顯示 DEBUG 層級日誌")
    g.add_argument("-i", "--input", required=True, help="輸入 MV 影片路徑（mp4/mkv/mov/webm）")
    g.add_argument("-o", "--output-dir",
                   help="輸出資料夾（預設為輸入影片所在目錄）")
    g.add_argument("--api-key",
                   help="DashScope API Key；預設依序讀取環境變數 DASHSCOPE_API_KEY、.env、"
                        "settings.json（優先序見 ADR-001）")
    g.add_argument("--model", default=DEFAULT_ASR_MODEL,
                   help=f"語音辨識模型（預設 {DEFAULT_ASR_MODEL}；"
                        "不支援 paraformer-v2，因其逐字時間為線性插值，見 ADR-004）")
    g.add_argument("-l", "--language",
                   help="語言提示 zh／yue／en（預設 zh）。yue 會被內部重映射為 zh，"
                        "避免觸發「粵語轉普通話」開關（ADR-001）")
    g.add_argument("--separate-vocals", action="store_true",
                   help="先做人聲分離再送辨識（提升辨識率，但需額外安裝 demucs；"
                        "未安裝時會提示而不會中斷）")
    add_style_args(g)
    g.add_argument("--max-chars", type=int, default=18,
                   help="每行字數上限（預設 18），僅作安全網；正常情況下以標點與句界斷行（ADR-013）")
    g.add_argument("--gap-threshold", type=int, default=800,
                   help="斷句停頓門檻，單位 ms（預設 800）：停頓超過此值即視為句界")
    g.add_argument("--hold-ms", type=int, default=300,
                   help="句尾停留時間，單位 ms（預設 300）：最後一字停留多久才消失，避免句子一閃即逝")
    g.add_argument("--min-clause-chars", type=int, default=MIN_CLAUSE_CHARS,
                   help=f"短句字數門檻（預設 {MIN_CLAUSE_CHARS}）：字數不超過此值且時長不足時，"
                        "才會與相鄰句合併（ADR-013）")
    g.add_argument("--min-clause-ms", type=int, default=MIN_CLAUSE_MS,
                   help=f"短句時長門檻，單位 ms（預設 {MIN_CLAUSE_MS}）：拖長音的短句仍會獨立成行，"
                        "以保留強調效果（ADR-013）")
    g.add_argument("--merge-max-chars", type=int, default=MERGE_MAX_CHARS,
                   help=f"合併後每行字數上限（預設 {MERGE_MAX_CHARS}）；"
                        "超出時改用 --max-chars 的切行規則（ADR-013 修訂）")
    g.add_argument("--keep-punctuation", action="store_true",
                   help="字幕保留標點（預設移除：行尾刪除、行中轉為空格，見 ADR-013）。"
                        "若希望歌詞與原曲完全一致可開啟")
    g.add_argument("--poll-interval", type=int, default=3,
                   help="辨識任務輪詢間隔，單位秒（預設 3）；網絡較慢時可調大")
    g.add_argument("--save-raw", action="store_true",
                   help="額外儲存一份 ASR 原始 JSON（除錯與比對用）")
    g.set_defaults(func=cmd_generate)

    # ---- render
    r = sub.add_parser("render", help="timings.json → ass（無須重跑 ASR）",
                       description="由 timings.json 重新產生 .ass；"
                                   "修改時間或文字後只需執行此命令。")
    r.add_argument("-v", "--verbose", action="store_true", help="顯示 DEBUG 層級日誌")
    r.add_argument("timings", help="*_timings.json 路徑（唯一真源）")
    r.add_argument("-o", "--output",
                   help="輸出 .ass 路徑（預設與 timings 同名，副檔名改為 .ass）")
    r.add_argument("--sync-style", action="store_true",
                   help="將本次樣式覆寫一併寫回 timings.json（永久生效，而非只本次輸出）")
    r.add_argument("--keep-punctuation", action="store_true",
                   help="字幕保留標點（預設移除：行尾刪除、行中轉為空格，見 ADR-013）")
    add_style_args(r, overrides=True)
    r.set_defaults(func=cmd_render)

    # ---- burn
    b = sub.add_parser("burn", help="影片 + ass → mp4（只接受 .ass，不會自動 render）",
                       description="以 ffmpeg 將字幕燒錄進畫面並重新編碼輸出 mp4。")
    b.add_argument("-v", "--verbose", action="store_true", help="顯示 DEBUG 層級日誌")
    b.add_argument("-i", "--input", required=True, help="原始 MV 影片")
    b.add_argument("-s", "--subtitle", required=True, help="要燒錄的 .ass 字幕檔")
    b.add_argument("-o", "--output",
                   help="輸出 mp4 路徑（預設為 <原檔名>_karaoke.mp4）")
    b.set_defaults(func=cmd_burn)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口：解析參數、設定 logging、執行子命令。

    Args:
        argv: 參數列表（``None`` = 用 ``sys.argv[1:]``，方便測試注入）。

    Returns:
        進程退出碼。0 成功；其餘由 :class:`KaraokeError.code` 決定
        （2 = 用法／環境、3 = 抽音、4 = ASR、5 = 燒錄）。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    # ⚠️ argparse subparser 嘅 default 會覆寫上層已設定嘅值，
    #    所以 -v 無論放喺 `main.py -v generate` 定 `main.py generate -v` 都要認得：
    #    直接預掃 argv 最穩陣（本工具冇其他 -v 參數）
    debug = any(tok in ("-v", "--verbose") for tok in argv)

    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="[%(asctime)s] %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return args.func(args)
    except KaraokeError as exc:
        # 每個拋出點都已經 LOGGER.error 過詳細原因（stderr / JSON），
        # 所以這裡只負責把例外轉回退出碼 —— 輸出與 v1.0.4 之前逐字相同，
        # 唔會多印一行，亦唔會出 traceback。
        #
        # ⚠️ 不變量：**新增拋出點時必須自己 LOGGER.error**，否則 CLI 會靜默
        #    失敗（只剩一個退出碼）。由 tests/test_error_taxonomy.py
        #    的 test_cli_error_paths_always_log 守著。
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
