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
  - docs/架構決策紀錄.md  ：ADR-001 ~ ADR-013

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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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


def hex_to_ass_color(hex_color: str) -> str:
    """``#RRGGBB`` → ``&HAABBGGRR``（ASS 顏色編碼，注意係 **BGR 反序**）。"""
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"顏色必須係 6 位 hex（#RRGGBB），收到：{hex_color!r}")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H00{b}{g}{r}".upper()


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

    def resolved(self) -> "StyleConfig":
        """把 0 值換算成相對 PlayResY 的預設值。"""
        margin_v = self.margin_v or max(24, round(self.play_res_y * 0.055))
        outline = self.outline_width or max(2.0, round(self.play_res_y / 360, 1))
        return replace(self, margin_v=margin_v, outline_width=outline)


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
    sys.exit(2)


def find_ffprobe() -> str:
    sibling = Path(find_ffmpeg()).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
    if sibling.is_file():
        return str(sibling)
    found = shutil.which("ffprobe")
    if found:
        return found
    LOGGER.error("搵唔到 ffprobe（通常同 ffmpeg 一齊安裝）。")
    sys.exit(2)


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
    proc = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", str(video), "-vn",
         "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0 or not out.is_file():
        LOGGER.error("ffmpeg 抽音失敗：\n%s", (proc.stderr or "")[-1500:])
        sys.exit(3)
    LOGGER.info("TempAudio 完成：%.2f MB", out.stat().st_size / 1_048_576)
    return out


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
        sys.exit(2)

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
        sys.exit(3)

    ffmpeg = find_ffmpeg()
    normalized = workdir / "vocals_16k.wav"
    proc = subprocess.run(
        [ffmpeg, "-y", "-v", "error", "-i", vocal,
         "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(normalized)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        LOGGER.error("人聲標準化失敗：\n%s", (proc.stderr or "")[-1500:])
        sys.exit(3)
    LOGGER.info("人聲分離完成：%s", normalized.name)
    return normalized


# ---------------------------------------------------------------------------
# Stage 3 · 本地音頻上傳（ADR-003）
# ---------------------------------------------------------------------------

def upload_audio(wav: Path) -> str:
    """上傳 TempAudio → UploadedURL。

    主路線：``Files.upload()`` 拎 ``file_id`` → ``Files.get()`` 拎**預簽名 https URL**。
    ⚠️ Files.upload 淨係畀 file_id，**唔係**可以直接餵畀 ASR 的 URL。
    後備：``OssUtils.upload()`` 返 ``oss://`` URL（需配合 resolve header）。
    """
    import dashscope

    LOGGER.info("上傳音訊到百煉臨時儲存 …")
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


def submit_and_wait_transcription(
    model: str,
    uploaded_url: str,
    language_hints: list[str] | None,
    poll_interval_s: int = 3,
    max_wait_s: int = 1800,
) -> dict[str, Any]:
    """提交 Filetrans 異步任務並輪詢至完成，回傳完整結果 JSON。

    ADR-002：**只傳** ``model`` / ``file_urls`` / ``language_hints``。
    ``enable_words`` / ``enable_itn`` 喺本 model 唔存在或無意義，禁止傳。
    """
    if model in BANNED_ASR_MODELS:
        LOGGER.error(
            "model %r 已永久禁用（ADR-004）：佢嘅「100% 逐字」係線性插值假時間軸。"
            "請用 %s", model, DEFAULT_ASR_MODEL
        )
        sys.exit(2)

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
        sys.exit(4)

    task_id = resp.output.task_id
    LOGGER.info("task_id = %s，開始輪詢 …", task_id)

    started = time.time()
    last_status = ""
    while True:
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
            sys.exit(4)
        if time.time() - started > max_wait_s:
            LOGGER.error("ASR 輪詢逾時（>%ds），task_id=%s", max_wait_s, task_id)
            sys.exit(4)
        time.sleep(poll_interval_s)

    # ⚠️ 必須逐筆檢查 subtask_status，淨係 check task_status 會漏錯
    results = fetched.output.get("results") or []        # 複數！唔好寫 output.result
    ok_items = [r for r in results if r.get("subtask_status") == "SUCCEEDED"]
    if not ok_items:
        LOGGER.error("沒有任何 subtask 成功：%s",
                     json.dumps(results, ensure_ascii=False, default=str)[:1200])
        sys.exit(4)

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
        sys.exit(4)

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
    )
    for name, value in (("primary_color", style.primary_color),
                        ("secondary_color", style.secondary_color),
                        ("outline_color", style.outline_color)):
        try:
            hex_to_ass_color(value)
        except ValueError as exc:
            LOGGER.error("timings.json 的 style.%s 有問題：%s", name, exc)
            sys.exit(2)

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
    return text.replace("{", "\\{").replace("}", "\\}")


def build_karaoke_ass(lines: list[LyricLine], style: StyleConfig, gap_ms: int = 50,
                      strip_punctuation: bool = True) -> str:
    """產生完整 ASS 文字（KaraokeAss）。

    ADR-013 標點處理（喺 **WordGroup 邊界**做，唔係逐字）：
      - ``strip_punctuation=True``（預設）：行尾標點**剝走**；行中標點變**半形空格**，
        並喺空格位插 ``{\\k<gap_cs>}`` 令高亮停一停（食住兩句之間嘅真實停頓）；
        下一詞已以空白開頭（英文）就唔補，避免雙空格
      - ``strip_punctuation=False``（``--keep-punctuation``）：標點原樣照出
        （輸出同 v1 完全一致 —— 標點黐喺前一個字嘅 syllable 度）
    """
    s = style.resolved()
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
    for line in lines:
        pieces: list[str] = []
        prev_end: int | None = None
        n_words = len(line.words)
        for wi, word in enumerate(line.words):
            for c in word.chars:
                # C-5 停頓補償：前一字 end 與本字 begin 相差 > gap_ms，插入不帶文字嘅 \k
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
        start = ms_to_ass_time(line.start_ms)       # ⚠️ ADR-005：必須 H:MM:SS.cc
        end = ms_to_ass_time(line.end_ms)
        events.append(f"Dialogue: 0,{start},{end},Karaoke,,0,0,0,,{''.join(pieces)}")

    return "\n".join(header + events) + "\n"


# ---------------------------------------------------------------------------
# Stage 11 · ffmpeg 燒錄（ADR-006 / ADR-009）
# ---------------------------------------------------------------------------

#: ffmpeg filter 有特殊語義、唔可以出現喺 ass 檔名字元（用於燒錄時的臨時改名）
_FILTER_UNSAFE_RE = re.compile(r"[=:,'\"\[\];\\]")


def burn_subtitles(
    ffmpeg: str,
    video: Path,
    ass_path: Path,
    output_path: Path,
    play_res_x: int,
    play_res_y: int,
    audio_codec: str | None,
) -> None:
    """把 ASS 燒錄進影片。

    ADR-006 鐵律：
      1. **永遠唔好**把路徑直接寫入 ``-vf ass=<path>``
         （Windows 嘅 ``C:`` 冒號會畀 ffmpeg 當 filter option 分隔符）。
         改為 ``cwd=<ass 所在目錄>`` + filter 只用**檔名**。
      2. ffmpeg 命令**必須**帶 ``original_size=WxH``。
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
            "-i", os.path.relpath(str(video), str(workdir)),
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

        out_arg = os.path.relpath(str(output_path), str(workdir))
        LOGGER.info("開始燒錄字幕（libx264 crf18 medium）…")
        proc = subprocess.run(base + audio_args + [out_arg], cwd=str(workdir),
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not output_path.is_file():
            LOGGER.warning("copy 音軌失敗，降級為 aac 192k 重試…\n%s", (proc.stderr or "")[-600:])
            proc = subprocess.run(base + ["-c:a", "aac", "-b:a", "192k", out_arg], cwd=str(workdir),
                                  capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not output_path.is_file():
            LOGGER.error("ffmpeg 燒錄失敗（rc=%s）：\n%s", proc.returncode, (proc.stderr or "")[-2500:])
            sys.exit(5)
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
    sys.exit(2)


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
                     progress_cb: ProgressCB | None = None) -> GenerateResult:
    """影片 → 百煉 ASR → timings.json + ass（唔出影片）。

    行為同舊 cmd_generate 完全一致（log 逐句相同）；錯誤路徑用 ``sys.exit``
    保持 CLI exit code —— UI 背景 thread 呼叫時要 ``except BaseException``。

    Args:
        opts: 全部 generate 參數。
        progress_cb: 階段回呼（可選），stage ∈ 抽音/上傳/ASR/斷句/渲染。

    Returns:
        GenerateResult：產出路徑 + 統計。

    Raises:
        SystemExit: 輸入唔存在 / API key 缺 / ASR 失敗等（保留 CLI exit code）。
    """
    video = opts.input
    if not video.is_file():
        LOGGER.error("輸入影片唔存在：%s", video)
        sys.exit(2)

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
        if opts.separate_vocals:
            if progress_cb:
                progress_cb("抽音", "人聲分離（首次需下載模型）…")
            asr_source = separate_vocals(asr_source, workdir)

        if progress_cb:
            progress_cb("上傳", "上傳音訊到百煉臨時儲存 …")
        uploaded_url = upload_audio(asr_source)

        hints, warn = resolve_language_hints(opts.language)
        if warn:
            LOGGER.warning(warn)
        if progress_cb:
            progress_cb("ASR", "提交異步轉寫任務並輪詢 …")
        raw = submit_and_wait_transcription(
            model=opts.model, uploaded_url=uploaded_url, language_hints=hints,
            poll_interval_s=opts.poll_interval_s,
        )
        raw_asr_path: Path | None = None
        if opts.save_raw:
            raw_asr_path = output_dir / f"{video.stem}_raw_asr.json"
            raw_asr_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        if progress_cb:
            progress_cb("斷句", "解析 ASR 結果、拆字、OpenCC、斷句 …")
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
            sys.exit(4)

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

        json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("timings.json 已輸出：%s", json_path)

        ass_text = build_karaoke_ass(lines, style,
                                     strip_punctuation=not opts.keep_punctuation)
        ass_path.write_text(ass_text, encoding="utf-8")
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
        return 2

    doc = json.loads(src.read_text(encoding="utf-8"))
    lines, style = timings_doc_to_lines(doc)

    # 樣式覆寫（本次 render 生效；想持久落 JSON 要加 --sync-style）
    overrides = {k: v for k, v in (
        ("font_name", args.font_name), ("font_size", args.font_size),
        ("primary_color", args.primary_color), ("secondary_color", args.secondary_color),
        ("outline_color", args.outline_color), ("margin_v", args.margin_v),
        ("alignment", args.alignment), ("outline_width", args.outline_width),
    ) if v is not None}
    if overrides:
        style = replace(style, **overrides)
        LOGGER.info("套用本次樣式覆寫：%s", ", ".join(overrides))

    if args.sync_style:
        raw_style = doc.setdefault("style", {})
        for key, value in overrides.items():
            raw_style[key] = value
        src.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
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

    out.write_text(build_karaoke_ass(lines, style,
                                     strip_punctuation=not args.keep_punctuation),
                   encoding="utf-8")
    LOGGER.info("render 完成：%s（%d 行）", out, len(lines))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: burn
# ---------------------------------------------------------------------------

def burn_video(video: Path, ass_path: Path, output_path: Path, *,
               progress_cb: ProgressCB | None = None) -> Path:
    """影片 + .ass → 燒錄 mp4（UI 入口；CLI 見 cmd_burn）。

    ADR-012：座標系由 .ass 自己讀出，唔使依賴 timings.json。

    Args:
        video: 原始 MV 影片。
        ass_path: 要燒錄嘅 .ass 檔。
        output_path: 輸出 mp4 路徑。
        progress_cb: 階段回呼（stage = "燒錄"）。

    Returns:
        輸出 mp4 路徑。

    Raises:
        SystemExit: 燒錄失敗（exit code 5，保留 CLI 行為）。
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
    if progress_cb:
        progress_cb("燒錄", "開始燒錄字幕（libx264 crf18 medium）…")
    burn_subtitles(ffmpeg=ffmpeg, video=video, ass_path=ass_path, output_path=output_path,
                   play_res_x=width, play_res_y=height, audio_codec=audio_codec)
    if progress_cb:
        progress_cb("燒錄", "完成")
    return output_path


def cmd_burn(args: argparse.Namespace) -> int:
    video = Path(args.input).expanduser()
    ass_path = Path(args.subtitle).expanduser()
    if not video.is_file():
        LOGGER.error("輸入影片唔存在：%s", video)
        return 2
    if not ass_path.is_file():
        LOGGER.error("字幕檔唔存在：%s", ass_path)
        return 2

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
    # UI 主頁「對齊」「描邊粗幼」對應項（ADR-015：主頁設定 = 燒錄權威）
    group.add_argument(
        "--alignment", type=int, choices=[2, 3, 5, 8],
        default=None if overrides else 2,
        help="字幕對齊位置（ASS Alignment 編號）：2 = 底部置中、3 = 中下、"
             "5 = 中部置中、8 = 頂部置中（預設 2）" + suffix)
    group.add_argument(
        "--outline-width", type=float, default=None if overrides else 0.0,
        help="描邊粗細，單位 px（預設 0 = 依 PlayResY 自動推算，一般等於字級的 4%）。"
             "畫面雜亂時可加粗至 3–4px" + suffix)


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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
