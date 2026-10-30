# -*- coding: utf-8 -*-
"""models — 純資料層：timings.json doc dict 嘅存取 / 編輯 / 正規化（N1–N7）。

EditorState 持有**完整 doc dict**（同 schema v3 形狀），呢個模組全部係純函數
（唔 import tkinter），方便 V2/V3/V4/V6/V7 做無 Tk unit test。
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

#: 相容讀入嘅 schema 版本（v1 = 標點黐 text 尾；v2 = 有獨立 punctuation；v3 = +manual/edited_at）
SUPPORTED_VERSIONS = (None, 1, 2, 3)

_CHAR_REF = re.compile(r"[\s，。、！？：；「」『』（）《》〈〉,.\"!?:;'()\[\]…—\-~—]")


# ---------------------------------------------------------------------------
# 讀寫
# ---------------------------------------------------------------------------

def load_doc(path: str | Path) -> dict[str, Any]:
    """讀 timings.json（v1/v2/v3 相容，只 warn 唔 fail —— CLI 同款行為）。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    version = doc.get("schema_version")
    if version not in (2, 3):
        print(f"[ui] timings.json schema_version={version!r}，自動相容處理")
    doc.setdefault("source", {})
    doc.setdefault("style", {})
    doc.setdefault("lines", [])
    return doc


def atomic_write_json(path: str | Path, doc: dict[str, Any]) -> None:
    """原子寫入 + 覆寫前留 .bak（E2 / R6 對策）。"""
    path = Path(path)
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        bak.write_bytes(path.read_bytes())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 存取 helper
# ---------------------------------------------------------------------------

def iter_chars(line: dict) -> Iterator[tuple[int, int, dict, dict]]:
    """順序吐 (word_i, char_i, char_dict, word_dict)。"""
    for word_i, word in enumerate(line.get("words") or []):
        for char_i, char in enumerate(word.get("chars") or []):
            yield word_i, char_i, char, word


def word_of(line: dict, word_i: int) -> dict | None:
    words = line.get("words") or []
    return words[word_i] if 0 <= word_i < len(words) else None


def char_of(line: dict, word_i: int, char_i: int) -> dict | None:
    word = word_of(line, word_i)
    if word is None:
        return None
    chars = word.get("chars") or []
    return chars[char_i] if 0 <= char_i < len(chars) else None


def display_text(line: dict, strip_punctuation: bool = True) -> str:
    """N1：由 words[] 重新組顯示文字（同 main.LyricLine.display_text 規則一致）。

    行尾標點剝走；行中標點變半形空格（下一詞以空白開頭就唔補，防雙空格）。
    """
    words = line.get("words") or []
    parts: list[str] = []
    n = len(words)
    for i, word in enumerate(words):
        parts.append(word.get("text", ""))
        punct = word.get("punctuation") or ""
        if not punct:
            continue
        if not strip_punctuation:
            parts.append(punct)
        elif i + 1 < n and not (words[i + 1].get("text", "") or "").startswith(" "):
            parts.append(" ")
    return "".join(parts)


def doc_stats(doc: dict) -> dict[str, Any]:
    """統計（R4：每次開檔即時重算，唔可以硬編碼比例）。"""
    num_lines = len(doc.get("lines") or [])
    num_chars = 0
    est_words = est_total = 0
    for line in doc.get("lines") or []:
        for word in line.get("words") or []:
            num_chars += len(word.get("chars") or [])
            est_total += 1
            if word.get("estimated"):
                est_words += 1
    return {
        "num_lines": num_lines,
        "num_chars": num_chars,
        "estimated_words": est_words,
        "estimated_total_words": est_total,
        "estimated_ratio": (est_words / est_total) if est_total else 0.0,
    }


def total_duration_ms(doc: dict) -> int:
    lines = doc.get("lines") or []
    if not lines:
        return 0
    return max(int(l.get("end_ms", 0)) for l in lines)


# ---------------------------------------------------------------------------
# 編輯操作（commands 會包住呢啲做 undo）
# ---------------------------------------------------------------------------

def proportional_split(begin_ms: int, end_ms: int, count: int) -> list[tuple[int, int]]:
    """[begin, end] 按字數均分成 count 段（每段至少 1ms；C6 場景 A 用）。"""
    if count <= 0:
        return []
    total = max(count, end_ms - begin_ms)
    out: list[tuple[int, int]] = []
    cursor = begin_ms
    step = total / count
    for i in range(count):
        nxt = begin_ms + round(step * (i + 1))
        if nxt <= cursor:
            nxt = cursor + 1
        out.append((cursor, nxt))
        cursor = nxt
    return out


def set_line_text(line: dict, new_text: str) -> None:
    """改一行歌詞文字（C1 表格改字）。

    - 字數不變 → 逐字對應替换（時間軸原封不動）
    - 字數改變 → 整行重建做一個 word，時間按字數均分，全部標 ``estimated=true``
      （誠實標示要人手校，唔會假裝 ASR 有依據）
    - 標點：新文字入面嘅標點剝出嚟存 ``punctuation``（N6），唔黐字
    """
    new_text = new_text.strip()
    chars = [c for _, _, c, _ in iter_chars(line)]
    old_puncts = [(w.get("punctuation") or "") for w in line.get("words") or []]
    old_text = "".join(c.get("text", "") for c in chars)

    if not new_text:
        return
    if len(new_text) == len(old_text) and chars:
        for c, ch in zip(chars, new_text):
            c["text"] = ch
        return

    # 字數變咗 → 重建單一 word
    begin = chars[0]["begin_ms"] if chars else int(line.get("start_ms", 0))
    end = chars[-1]["end_ms"] if chars else int(line.get("end_ms", 0))
    spans = proportional_split(begin, end, len(new_text))
    word = {
        "text": new_text,
        "begin_ms": begin,
        "end_ms": end,
        "estimated": True,
        "punctuation": "",
        "chars": [{"text": ch, "begin_ms": b, "end_ms": e}
                  for ch, (b, e) in zip(new_text, spans)],
    }
    # 尾標點搬去 punctuation（N6）
    m = re.search(r"[，。！？；：、…,!?:;]+$", new_text)
    if m and m.start() > 0:
        word["text"] = new_text[:m.start()]
        word["punctuation"] = m.group(0)
        word["chars"] = word["chars"][:m.start()]
    line["words"] = [word]


def split_line_at_word(line: dict, word_i: int) -> dict | None:
    """喺 word 邊界斷句（C5）。回傳新行（未插入 doc）；原行縮短。

    新行 start_ms = 下一個 word 嘅 begin_ms；前行 end_ms 唔改（兩行時間重疊
    由 normalize N5 clip）。
    """
    words = line.get("words") or []
    if not (0 < word_i < len(words)):
        return None
    head, tail = words[:word_i], words[word_i:]
    new_line = {
        "start_ms": tail[0].get("begin_ms", 0),
        "end_ms": int(line.get("end_ms", 0)),
        "text": "",                       # N1 重組
        "break_reason": "manual",         # schema v3
        "words": tail,
    }
    line["words"] = head
    line["end_ms"] = head[-1].get("end_ms", line.get("end_ms", 0)) if head else line["end_ms"]
    line["break_reason"] = line.get("break_reason") or ""
    return new_line


def merge_lines(first: dict, second: dict) -> None:
    """合併 second 入 first（C5）：保留所有 char 原始時間軸；中間空隙唔填。"""
    first["words"] = (first.get("words") or []) + (second.get("words") or [])
    first["end_ms"] = second.get("end_ms", first.get("end_ms", 0))
    first["break_reason"] = "manual"


def insert_word(line: dict, word_i: int, text: str,
                begin_ms: int | None = None, end_ms: int | None = None) -> dict:
    """行中插入一個字（C6 場景 B）：新單字 WordGroup，estimated=true；
    原 char 時間軸不變（唔推遞）。"""
    words = line.get("words") or []
    if begin_ms is None or end_ms is None:
        # 預設：插喺 word_i 前面，用前後 word 之間嘅中點做 1ms 佔位
        prev_end = words[word_i - 1].get("end_ms", 0) if word_i > 0 else int(line.get("start_ms", 0))
        next_begin = words[word_i].get("begin_ms", prev_end + 1) if word_i < len(words) else prev_end + 1
        mid = max(prev_end, (prev_end + next_begin) // 2)
        begin_ms, end_ms = mid, mid + 1
    word = {
        "text": text, "begin_ms": begin_ms, "end_ms": end_ms,
        "estimated": True, "punctuation": "",
        "chars": [{"text": text, "begin_ms": begin_ms, "end_ms": end_ms}],
    }
    words.insert(word_i, word)
    return word


def add_line(doc: dict, start_ms: int, end_ms: int, text: str, at_line_i: int | None = None) -> dict:
    """新增整行（C6 場景 A）：按字數均分、全部 estimated=true（要人手校）。"""
    text = _CHAR_REF.sub("", text) if False else text   # 保留原字（標點由下面處理）
    spans = proportional_split(start_ms, end_ms, max(1, len(text)))
    tail = re.search(r"[，。！？；：、…,!?:;]+$", text)
    pure = text[:tail.start()] if (tail and tail.start() > 0) else text
    spans = spans[:len(pure)] or spans
    spans = proportional_split(start_ms, end_ms, max(1, len(pure)))
    word = {
        "text": pure, "begin_ms": start_ms, "end_ms": end_ms,
        "estimated": True, "punctuation": (tail.group(0) if tail else ""),
        "chars": [{"text": ch, "begin_ms": b, "end_ms": e}
                  for ch, (b, e) in zip(pure, spans)],
    }
    line = {"start_ms": start_ms, "end_ms": end_ms, "text": pure,
            "break_reason": "manual", "words": [word]}
    lines = doc.setdefault("lines", [])
    idx = len(lines) if at_line_i is None else max(0, min(at_line_i, len(lines)))
    # 插入位置按時間排序搵位
    if at_line_i is None:
        idx = len(lines)
        for i, l in enumerate(lines):
            if int(l.get("start_ms", 0)) > start_ms:
                idx = i
                break
    lines.insert(idx, line)
    return line


def respread_word(line: dict, word_i: int) -> bool:
    """重新劃分（C4）：word [begin,end] 內部 char 按字數比例重分 → 清 estimated。

    人手拖過嘅不均 char 時間會被重置 —— 呢個係「重新劃分」嘅語意。
    """
    word = word_of(line, word_i)
    if word is None or not (word.get("chars") or []):
        return False
    begin, end = int(word.get("begin_ms", 0)), int(word.get("end_ms", 0))
    if end <= begin:
        return False
    spans = proportional_split(begin, end, len(word["chars"]))
    for char, (b, e) in zip(word["chars"], spans):
        char["begin_ms"], char["end_ms"] = b, e
    word["estimated"] = False
    return True


def has_estimated(line: dict) -> bool:
    """呢行有冇估算字（篩選 chip 用，§4.4）。"""
    return any(w.get("estimated") for w in line.get("words") or [])


# ---------------------------------------------------------------------------
# 正規化（§7.2 N1–N7，儲存前必須跑）
# ---------------------------------------------------------------------------

def normalize(doc: dict) -> None:
    """N1–N7（in-place）。重疊 clip、倒轉夾返、text 重建、end_ms + hold_ms。"""
    hold_ms = int((doc.get("source") or {}).get("hold_ms") or 300)
    lines = doc.get("lines") or []

    prev_start_next: int | None = None
    for i, line in enumerate(lines):
        words = line.get("words") or []
        # N4：word / char 唔可倒轉、begin < end
        last_end: int | None = None
        for word in words:
            chars = word.get("chars") or []
            for c in chars:
                b, e = int(c.get("begin_ms", 0)), int(c.get("end_ms", 0))
                if last_end is not None and b < last_end:
                    b = last_end          # 越界夾返入相鄰 char 範圍
                if e <= b:
                    e = b + 1
                c["begin_ms"], c["end_ms"] = b, e
                last_end = e
            if chars:
                word["begin_ms"], word["end_ms"] = chars[0]["begin_ms"], chars[-1]["end_ms"]
        # N2 / N1
        if words:
            line["start_ms"] = words[0]["begin_ms"]
            line["end_ms"] = words[-1]["end_ms"]
        line["text"] = display_text(line)

    # N5：行與行唔可以重疊（第 N 行 end ≤ 第 N+1 行 start）→ clip 向後推
    for i in range(len(lines) - 1):
        nxt_start = int(lines[i + 1].get("start_ms", 0))
        if int(lines[i].get("end_ms", 0)) > nxt_start:
            lines[i]["end_ms"] = nxt_start

    # N3：end_ms = 尾 char end + hold_ms，clip 唔好超過下一行 start_ms
    for i, line in enumerate(lines):
        if not (line.get("words") or []):
            continue
        next_start = int(lines[i + 1].get("start_ms", 0)) if i + 1 < len(lines) else None
        end = int(line["end_ms"]) + hold_ms
        if next_start is not None:
            end = min(end, next_start)
        line["end_ms"] = max(int(line["start_ms"]) + 1, end)

    # 統計欄位 + 編輯痕跡（schema v3）
    doc["schema_version"] = 3
    source = doc.setdefault("source", {})
    source.setdefault("hold_ms", hold_ms)
    source["edited_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source.setdefault("edited_by", "ui")
