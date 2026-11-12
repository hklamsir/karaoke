# -*- coding: utf-8 -*-
"""models — 純資料層：timings.json doc dict 嘅存取 / 編輯 / 正規化（N1–N7）。

EditorState 持有**完整 doc dict**（同 schema v3 形狀），呢個模組全部係純函數
（唔 import tkinter），方便 V2/V3/V4/V6/V7 做無 Tk unit test。
"""

from __future__ import annotations

import copy
import json
import re
import statistics
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
    # 開檔即實體化「標點 → 空格」，令歌詞樹／逐字微調／預覽三邊一致
    materialize_punct_spaces(doc)
    return doc


def atomic_write_json(path: str | Path, doc: dict[str, Any]) -> None:
    """原子寫入 + 覆寫前留 .bak（E2 / R6 對策）。

    實作已統一在 :func:`main.atomic_write_json`（審查報告 §2.8）—— 以前 UI 與
    CLI 各自一份，行為已經分叉（UI 有 .bak、CLI 直接覆寫）。PR-2 拆
    ``karaoke/`` package 時會搬到 ``karaoke/io.py``，屆時雙方一齊改 import。
    """
    import main          # 延遲 import：models 係純資料層，唔想 module 載入時就拉入 CLI

    main.atomic_write_json(path, doc)


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


def materialize_punct_spaces(doc: dict) -> int:
    """把「詞尾標點 → 半形空格」規則實體化成一個真 char（idempotent）。

    用戶 2026-11-11 回報：歌詞樹顯示「逝去種種 昨日經已死」有個空格，但逐字微調
    同預覽都冇 —— 因為嗰個空格只係 :func:`display_text` 由 ``punctuation`` **即場
    推導**出嚟，``chars[]`` 冇呢個字，所以微調表、Canvas 疊圖都唔會畫。

    做法：喺下一個詞嘅 chars **最前面**插一個 ``' '``（1ms，時間 = 該詞 begin）。
    咁樣三邊自然一致：
      - ``display_text``：下一詞已以空白開頭 → 唔會再加第二個
      - ``main._karaoke_pieces``：``nxt.text.startswith(" ")`` → 唔會再加第二個
      - 疊圖／微調：見到真 char，照畫

    Returns:
        今次插入咗幾個空格 char。
    """
    count = 0
    for line in doc.get("lines") or []:
        words = line.get("words") or []
        for i, word in enumerate(words[:-1]):
            if not (word.get("punctuation") or ""):
                continue
            nxt = words[i + 1]
            chars = nxt.get("chars") or []
            if not chars or (chars[0].get("text") or "").startswith(" "):
                continue
            begin = int(chars[0].get("begin_ms", 0))
            chars.insert(0, {"text": " ", "begin_ms": begin, "end_ms": begin + 1})
            nxt["chars"] = chars
            nxt["text"] = " " + (nxt.get("text") or "")
            count += 1
    return count


def display_text(line: dict, strip_punctuation: bool = True) -> str:
    """N1：由 words[] 重新組顯示文字（同 main.LyricLine.display_text 規則一致）。

    行尾標點剝走；行中標點變半形空格（下一詞以空白開頭就唔補，防雙空格）。

    空白政策（2026-11-11 修訂）：**折疊成一個、去頭尾，但唔會再全刪**。
    舊版 `re.sub(r"\\s+", "")` 會連用戶刻意打嘅半形空格（例如「種種 昨日」）
    都刪走，但 `chars[]` 入面仍然有 `' '` 呢個 char —— 結果歌詞樹／字幕卡
    冇空格、燒錄同預覽反而有罅，UI 同成品唔一致（用戶回報 #2）。而家
    `word["text"]` 一律由 chars 重建，殘留雜空白只會係一個 char，
    所以保留一個空格係安全嘅，唔會再出「幽靈空格」。
    """
    words = line.get("words") or []
    parts: list[str] = []
    n = len(words)
    for i, word in enumerate(words):
        parts.append(re.sub(r"\s+", " ", word.get("text") or ""))
        punct = word.get("punctuation") or ""
        if not punct:
            continue
        if not strip_punctuation:
            parts.append(punct)
        elif i + 1 < n and not (words[i + 1].get("text", "") or "").startswith(" "):
            parts.append(" ")
    # 詞與詞接合後可能出現連續空白 → 再折疊一次；頭尾空白冇意義，清走
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def doc_stats(doc: dict) -> dict[str, Any]:
    """統計（R4：每次開檔即時重算，唔可以硬編碼比例）。"""
    num_lines = len(doc.get("lines") or [])
    num_chars = 0
    zero_chars = 0          # 時長 <= 1ms 的字：AI 漏詞／時長崩壞的直接徵狀
    est_words = est_total = 0
    for line in doc.get("lines") or []:
        for word in line.get("words") or []:
            chars = word.get("chars") or []
            num_chars += len(chars)
            for c in chars:
                if int(c.get("end_ms", 0)) - int(c.get("begin_ms", 0)) <= 1:
                    zero_chars += 1
            est_total += 1
            if word.get("estimated"):
                est_words += 1
    return {
        "num_lines": num_lines,
        "num_chars": num_chars,
        "zero_chars": zero_chars,
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
    new_text = re.sub(r"\s+", " ", new_text.strip())   # 連續空白折疊（同 display_text 一致）
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


def split_word_at_char(line: dict, word_i: int, char_i: int) -> int | None:
    """把一個 WordGroup 喺 ``char_i`` **之後**切成兩個（時間軸原封不動）。

    尾段會佔 ``word_i + 1``；原標點跟尾段（標點永遠屬最後一個字）。

    Args:
        line: 目標行（原地修改）。
        word_i: 目標詞索引。
        char_i: 由 0 起算，斷喺呢個字之後。

    Returns:
        前半段嘅 word 索引（= ``word_i``）；已係詞尾就唔切、照回 ``word_i``；
        參數唔合法回傳 ``None``。
    """
    words = line.get("words") or []
    if not (0 <= word_i < len(words)):
        return None
    word = words[word_i]
    chars = word.get("chars") or []
    if not chars:
        return None
    char_i = max(0, min(char_i, len(chars) - 1))
    if char_i >= len(chars) - 1:
        return word_i                      # 已經係詞尾，唔使切
    head_chars, tail_chars = chars[:char_i + 1], chars[char_i + 1:]
    head_end = int(head_chars[-1].get("end_ms", word.get("end_ms", 0)))
    tail_begin = int(tail_chars[0].get("begin_ms", head_end))
    head_word = {
        "text": "".join(c.get("text", "") for c in head_chars),
        "begin_ms": int(word.get("begin_ms", head_chars[0].get("begin_ms", 0))),
        "end_ms": head_end,
        "estimated": bool(word.get("estimated")),
        "punctuation": "",                     # 標點永遠跟最後一個字
        "chars": head_chars,
    }
    tail_word = {
        "text": "".join(c.get("text", "") for c in tail_chars),
        "begin_ms": tail_begin,
        "end_ms": int(word.get("end_ms", tail_chars[-1].get("end_ms", tail_begin))),
        "estimated": bool(word.get("estimated")),
        "punctuation": word.get("punctuation") or "",
        "chars": tail_chars,
    }
    words[word_i:word_i + 1] = [head_word, tail_word]
    return word_i


def split_line_at_char(line: dict, word_i: int, char_i: int) -> dict | None:
    """喺**字**邊界斷句（C5b）：可以切穿一個詞，時間軸原封不動。

    ASR 只回 word 級時間，但每個 CharSlot 本身已有 begin/end（``word_slot_to_group``
    按字數比例分好），所以切穿一個詞係**無損**嘅，唔需要重新估時間。

    Args:
        line: 目標行（會被就地修改成「前半行」）。
        word_i: 目標詞索引。
        char_i: 由 0 起算，斷喺呢個字**之後**。

    Returns:
        新行（未插入 doc）；索引／參數唔合法回傳 ``None``。
    """
    words = line.get("words") or []
    if not (0 <= word_i < len(words)):
        return None
    head_i = split_word_at_char(line, word_i, char_i)
    if head_i is None:
        return None
    return split_line_at_word(line, head_i + 1)


def merge_lines(first: dict, second: dict) -> None:
    """合併 second 入 first（C5）：保留所有 char 原始時間軸；中間空隙唔填。"""
    first["words"] = (first.get("words") or []) + (second.get("words") or [])
    first["end_ms"] = second.get("end_ms", first.get("end_ms", 0))
    first["break_reason"] = "manual"


def insert_word(line: dict, word_i: int, text: str,
                begin_ms: int | None = None, end_ms: int | None = None,
                duration_ms: int = 100) -> dict:
    """喺 ``word_i`` **之前**插入一個字（C6 場景 B）：新單字 WordGroup，estimated=true。

    時間：預設貼住下一個字（``end = 下一字 begin``、長度 = ``duration_ms``），
    唔夠位就夾返前一個字之後。原 char 時間軸不變（唔推遞）。
    """
    words = line.get("words") or []
    if begin_ms is None or end_ms is None:
        prev_end = (int(words[word_i - 1].get("end_ms", 0)) if word_i > 0
                    else int(line.get("start_ms", 0)))
        next_begin = (int(words[word_i].get("begin_ms", prev_end + 1))
                      if word_i < len(words) else prev_end + 1)
        dur = max(1, int(duration_ms))
        begin_ms = max(prev_end, next_begin - dur)
        end_ms = max(begin_ms + 1, next_begin)
    word = {
        "text": text, "begin_ms": begin_ms, "end_ms": end_ms,
        "estimated": True, "punctuation": "",
        "chars": [{"text": text, "begin_ms": begin_ms, "end_ms": end_ms}],
    }
    words.insert(word_i, word)
    return word


def insert_char_after(line: dict, word_i: int, char_i: int, text: str,
                      duration_ms: int = 100, ripple: bool = False) -> dict | None:
    """喺某個字**之後**插入一個字（C6 場景 B'：行尾／字尾加字）。

    用戶 2026-11-11 回報：想喺最後一個字（例如「記」）後面加一個字，時間約 0.1 秒
    （主頁可設定），舊版只可以插喺選中字**前面**而且時長 1ms（等於睇唔到）。

    行為：
      - 唔係詞尾 → 先切開個詞（時間原封不動），新字插喺兩段之間
      - 新字時間 = 前字 end 起、長 ``duration_ms``
      - 撞到後面已有嘅字：``ripple=True`` 就整段順延，否則夾到剛好唔重疊
        （最短 1ms；最終由 :func:`normalize` 收尾）

    Args:
        line: 目標行（原地修改）。
        word_i: 目標詞索引。
        char_i: 由 0 起算，插喺呢個字之後。
        text: 新字（多字會變一個多字 WordGroup，但多數字用家在行尾加單字）。
        duration_ms: 新字時長（ms），預設 100 = 0.1 秒。
        ripple: 撞到後面的字時，是否整段順延（同「連動」勾選一致）。

    Returns:
        新 WordGroup；參數唔合法回傳 ``None``。
    """
    words = line.get("words") or []
    if not words:
        return None
    word_i = max(0, min(word_i, len(words) - 1))
    chars = words[word_i].get("chars") or []
    if not chars:
        return None
    head_i = split_word_at_char(line, word_i, char_i)
    if head_i is None:
        return None
    head_chars = words[head_i].get("chars") or []
    begin = int(head_chars[-1].get("end_ms", words[head_i].get("end_ms", 0)))
    dur = max(1, int(duration_ms))
    end = begin + dur
    was_last = head_i == len(words) - 1
    nxt_chars = words[head_i + 1].get("chars") or [] if head_i + 1 < len(words) else []
    if nxt_chars:
        nxt_begin = int(nxt_chars[0].get("begin_ms", end))
        if nxt_begin < end:
            if ripple:
                shift = end - nxt_begin
                for word in words[head_i + 1:]:
                    for c in word.get("chars") or []:
                        c["begin_ms"] = int(c["begin_ms"]) + shift
                        c["end_ms"] = int(c["end_ms"]) + shift
                    word["begin_ms"] = int(word.get("begin_ms", 0)) + shift
                    word["end_ms"] = int(word.get("end_ms", 0)) + shift
            else:
                end = max(begin + 1, nxt_begin)
    new_word = {
        "text": text, "begin_ms": begin, "end_ms": end,
        "estimated": True, "punctuation": "",
        "chars": [{"text": text, "begin_ms": begin, "end_ms": end}],
    }
    # ⚠️ 加喺行尾：原本最後一個字嘅**句末標點要跟住新字**。唔搬嘅話
    # display_text 會當「行中標點」補一個空格 → 用戶見到「記 住」（2026-11-11 回報）。
    if was_last:
        tail_punct = words[head_i].get("punctuation") or ""
        if tail_punct:
            words[head_i]["punctuation"] = ""
            new_word["punctuation"] = tail_punct
    words.insert(head_i + 1, new_word)
    line["end_ms"] = max(int(line.get("end_ms") or 0),
                         int(words[-1].get("end_ms") or 0))
    return new_word


def insert_char_before(line: dict, word_i: int, char_i: int, text: str,
                       duration_ms: int = 100, ripple: bool = False) -> dict | None:
    """喺某個字**之前**插入一個字（逐字微調「＋字前」）。

    2026-11-11 用戶回報：選「去」（「逝去」係**一個**兩字詞）按「＋字前」，
    新字變咗插喺「逝」之前 —— 舊版 ``insert_word()`` 只收 ``word_i``，永遠插喺
    整個詞之前。正解：先喺目標字之前切詞（時間無損），再插喺兩段之間。

    新字時間：貼住目標字開始（``end = 目標字 begin``），長 ``duration_ms``；
    前面唔夠位就縮短；``ripple=True`` 就整段順延。

    Args:
        line: 目標行（原地修改）。
        word_i: 目標詞索引。
        char_i: 由 0 起算，插喺呢個字之前。
        text: 新字。
        duration_ms: 新字時長（ms），預設 100。
        ripple: 前面唔夠位時是否把目標字及之後整段順延。

    Returns:
        新 WordGroup；參數唔合法回傳 ``None``。
    """
    words = line.get("words") or []
    if not (0 <= word_i < len(words)):
        return None
    chars = words[word_i].get("chars") or []
    if not chars:
        return None
    char_i = max(0, min(char_i, len(chars) - 1))
    if char_i > 0:
        head_i = split_word_at_char(line, word_i, char_i - 1)
        if head_i is None:
            return None
        target_i = head_i + 1
    else:
        target_i = word_i
    target_chars = words[target_i].get("chars") or []
    end = int(target_chars[0].get("begin_ms", 0)) if target_chars else 0
    prev_end = int(line.get("start_ms", 0))
    if target_i > 0:
        prev_chars = words[target_i - 1].get("chars") or []
        if prev_chars:
            prev_end = int(prev_chars[-1].get("end_ms", prev_end))
    dur = max(1, int(duration_ms))
    if ripple and end - prev_end < dur:
        shift = dur - max(0, end - prev_end)
        for word in words[target_i:]:
            for c in word.get("chars") or []:
                c["begin_ms"] = int(c["begin_ms"]) + shift
                c["end_ms"] = int(c["end_ms"]) + shift
            word["begin_ms"] = int(word.get("begin_ms", 0)) + shift
            word["end_ms"] = int(word.get("end_ms", 0)) + shift
        end += shift
    length = min(dur, max(1, end - prev_end))
    begin = max(0, end - length)
    new_word = {
        "text": text, "begin_ms": begin, "end_ms": end,
        "estimated": True, "punctuation": "",
        "chars": [{"text": text, "begin_ms": begin, "end_ms": end}],
    }
    words.insert(target_i, new_word)
    line["start_ms"] = min(int(line.get("start_ms") or begin), begin)
    return new_word


def set_char_text(line: dict, word_i: int, char_i: int, text: str) -> bool:
    """改**一個字**的文字（逐字微調「✏ 修改」）。

    用戶 2026-11-11 明確要求：**只可以輸入一個字**，多過一個字一律拒絕
    （要加字請用「+字前／+字後」，要改整句用歌詞樹「✏ 修改」）。

    空白字元（半形空格 ``" "``、全形空格 ``"　"``）**算合法**——用戶要用佢
    做可見的間隔。所以呢度唔可以 ``strip()`` 或者用 regex 清空白。

    Returns:
        ``True`` = 已改；``False`` = 參數唔合法／輸入唔係剛好一個字元。
    """
    words = line.get("words") or []
    if not (0 <= word_i < len(words)):
        return False
    chars = words[word_i].get("chars") or []
    if not (0 <= char_i < len(chars)):
        return False
    if text is None or len(text) != 1:
        return False
    char = chars[char_i]
    char["text"] = text
    word = words[word_i]
    word["chars"] = chars
    word["text"] = "".join(c.get("text", "") for c in chars)
    word["begin_ms"] = int(chars[0].get("begin_ms", word.get("begin_ms", 0)))
    word["end_ms"] = int(chars[-1].get("end_ms", word.get("end_ms", 0)))
    return True


def delete_char(line: dict, word_i: int, char_i: int) -> bool:
    """刪除一個字（逐字微調「✕ 刪字」，2026-11-11 用戶需求 #3）。

    - 詞內仲有其他字 → 只刪該 char，重算詞／行邊界
    - 詞變空 → 連個 WordGroup 一齊移除
    - 行只剩一個字 → **拒絕**（回傳 ``False``，請用「🗑 刪行」）

    Returns:
        ``True`` = 已刪；``False`` = 唔可以刪（參數錯／剩最後一個字）。
    """
    words = line.get("words") or []
    if not (0 <= word_i < len(words)):
        return False
    word = words[word_i]
    chars = word.get("chars") or []
    if not (0 <= char_i < len(chars)):
        return False
    total = sum(len(w.get("chars") or []) for w in words)
    if total <= 1:
        return False

    del chars[char_i]
    if chars:
        word["chars"] = chars
        word["begin_ms"] = int(chars[0].get("begin_ms", word.get("begin_ms", 0)))
        word["end_ms"] = int(chars[-1].get("end_ms", word.get("end_ms", 0)))
        word["text"] = "".join(c.get("text", "") for c in chars)
    else:
        punct = word.get("punctuation") or ""
        words.pop(word_i)
        # 標點永遠屬最後一個字：被刪嘅係尾詞就搬去新尾詞
        if punct and words:
            words[-1]["punctuation"] = (words[-1].get("punctuation") or "") + punct
    if words:
        line["start_ms"] = int(words[0].get("begin_ms", line.get("start_ms", 0)))
        line["end_ms"] = int(words[-1].get("end_ms", line.get("end_ms", 0)))
    return True


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
# 跨字均分 / 修補（用戶 2026-11-03 回報：AI 漏字後中間字冇時長）
#
# 背景：ASR 只吐 word 級時間，`word_slot_to_group()` 只在**詞內部**按字數平分。
# 一旦整段詞被漏掉（「那裡保證何時能突破」），呢批字之間完全冇時間依據；
# `respread_word()` 亦只作用於單一個 word 內部，跨詞的缺口永遠冇人填。
# 下面三個函數就係補呢個洞 —— 全部純資料操作，無 Tk，可直接單測。
# ---------------------------------------------------------------------------

#: 單字時長超過「本行中位數 × 此倍數」就當係崩壞。漏字段的典型徵狀係
#: 「一堆 100ms 字 + 一個 10 秒字」；正常長音拖 2–3 倍中位數唔會誤中。
REPAIR_STRETCH_RATIO = 6.0
#: 上述倍數的絕對下限：中位數細到 100ms 時 ×6 只有 600ms，太易誤中。
REPAIR_STRETCH_FLOOR_MS = 1500


def iter_flat_chars(line: dict) -> list[tuple[int, int, dict]]:
    """按播放次序攤平 ``(word_i, char_i, char)``。

    逐字面板的選取可以**跨詞**（一句歌詞通常 3–6 個詞），所以均分要用一個
    跨 ``words[]`` 的單一索引；``iter_chars()`` 係 generator，每次都要重跑。
    """
    return [(wi, ci, c) for wi, ci, c, _w in iter_chars(line)]


def flat_index_map(line: dict) -> dict[tuple[int, int], int]:
    """``(word_i, char_i) → 攤平索引``（面板 Treeview iid 轉索引用）。"""
    return {(wi, ci): k for k, (wi, ci, _c) in enumerate(iter_flat_chars(line))}


def _edge_word(line: dict, *, first: bool) -> dict | None:
    """本行第一個／最後一個**有 char** 的 word；冇 → ``None``。"""
    order = line.get("words") or []
    if not first:
        order = list(reversed(order))
    for word in order:
        if word.get("chars"):
            return word
    return None


def sync_word_bounds(line: dict) -> None:
    """word 的 ``[begin_ms, end_ms]`` 由首尾 char 推導，並連動行邊界（ADR-040）。

    行邊界以**差量**跟隨首／尾字移動，而唔係重推 —— 因為 ``line["end_ms"]``
    通常含句尾 hold（``normalize()`` 的 N3 把 ``hold_ms`` 加在最後一個字之後），
    直接重推會靜靜吞走那幾百毫秒的停留。

    差量的比較基準是**舊的 word 邊界**：呼叫者（``NudgeChars`` 等）一定係先改
    char 再叫呢個函式，所以 char 已是新值、word 仍是舊值 —— 這個差就是本次
    改動量。只改中間字時首尾 word 的差為 0，行邊界原封不動。

    Args:
        line: 一行歌詞 dict（in-place 修改）。
    """
    first_word = _edge_word(line, first=True)
    last_word = _edge_word(line, first=False)
    old_first_begin = (first_word.get("begin_ms") if first_word else None)
    old_last_end = (last_word.get("end_ms") if last_word else None)

    for word in line.get("words") or []:
        chars = word.get("chars") or []
        if chars:
            word["begin_ms"] = int(chars[0]["begin_ms"])
            word["end_ms"] = int(chars[-1]["end_ms"])

    if first_word is not None and old_first_begin is not None:
        delta = int(first_word["begin_ms"]) - int(old_first_begin)
        if delta:
            line["start_ms"] = max(0, int(line.get("start_ms") or 0) + delta)
    if last_word is not None and old_last_end is not None:
        delta = int(last_word["end_ms"]) - int(old_last_end)
        if delta:
            line["end_ms"] = max(0, int(line.get("end_ms") or 0) + delta)


def span_plan(line: dict, i_from: int, i_to: int, *,
              keep_ends: bool = True) -> dict[str, Any] | None:
    """試算「均分」結果但**唔改資料**（面板要喺 push command 前防呆）。

    Args:
        line: 一行歌詞 dict。
        i_from / i_to: 攤平字索引（次序不拘）。
        keep_ends: ``True`` = 首字、末字時間原封不動，只攤中間；
            ``False`` = 連首末字一齊由 ``[首.begin, 末.end]`` 重算。

    Returns:
        ``{"begin", "end", "count", "kind", "per_char_ms"}``；選取不足、
        索引越界、或中間冇位可攤（首字 end ≥ 末字 begin）→ ``None``。
    """
    chars = iter_flat_chars(line)
    lo, hi = sorted((int(i_from), int(i_to)))
    if lo < 0 or hi >= len(chars) or hi <= lo:
        return None
    if keep_ends and hi - lo < 2:
        return None                      # 冇中間字可以攤
    first_c, last_c = chars[lo][2], chars[hi][2]
    if keep_ends:
        begin, end = int(first_c["end_ms"]), int(last_c["begin_ms"])
        count, kind = hi - lo - 1, "middle"
    else:
        begin, end = int(first_c["begin_ms"]), int(last_c["end_ms"])
        count, kind = hi - lo + 1, "span"
    if count <= 0 or end - begin < count:
        return None                      # 每個字至少要分到 1ms
    return {"begin": begin, "end": end, "count": count, "kind": kind,
            "per_char_ms": round((end - begin) / count, 1)}


def redistribute_span(line: dict, i_from: int, i_to: int, *,
                      keep_ends: bool = True) -> dict[str, Any] | None:
    """把攤平索引 ``[i_from, i_to]`` 的逐字時間平均分配（跨詞 C4+）。

    兩個模式對應兩種真實情境：

    - ``keep_ends=True``：用戶已用耳對好兩個錨點（例如把尾字拖到正確收尾），
      只係中間幾個字仲疊埋一秒內 → 保留首末字，中間平均攤。
    - ``keep_ends=False``：整段時長崩壞（漏字），用 ``[首.begin, 末.end]``
      全段重攤，連端點都重算。

    ⚠️ 分配結果**仍然係估算**（冇 ASR 依據）→ 受影響 word 標
    ``estimated=True``，面板以橙色顯示，提醒仲要人手覆核。

    Args:
        line: 一行歌詞 dict（in-place 改）。
        i_from / i_to: 攤平字索引。
        keep_ends: 見上。

    Returns:
        同 :func:`span_plan`；``None`` = 冇改動（呼叫方唔應該 push undo）。
    """
    plan = span_plan(line, i_from, i_to, keep_ends=keep_ends)
    if plan is None:
        return None
    chars = iter_flat_chars(line)
    lo, hi = sorted((int(i_from), int(i_to)))
    targets = chars[lo + 1:hi] if keep_ends else chars[lo:hi + 1]
    spans = proportional_split(plan["begin"], plan["end"], len(targets))
    touched: set[int] = set()
    for (wi, _ci, c), (b, e) in zip(targets, spans):
        c["begin_ms"], c["end_ms"] = b, e
        touched.add(wi)
    sync_word_bounds(line)
    for wi in touched:
        line["words"][wi]["estimated"] = True
    return plan


def shift_range(line: dict, i_from: int, i_to: int, delta_ms: int) -> int:
    """把攤平索引 ``[i_from, i_to]`` 內所有字整體平移 ``delta_ms``（字距不變）。

    用喺「連動順延」：改一個字之後要為它騰位，但唔想逐個字跟住改。字與字之間
    的相對間距保持 —— 只係整段搬屋，唔會重新分配。

    任何字會被推到 0 之前就**整體少移**（保持相對間距）：寧願唔夠位，都好過
    出現負時間。上界唔夾 —— 推到後面由 ``normalize()`` N5 喺存檔時收口。

    Args:
        line: 一行歌詞（in-place）。
        i_from / i_to: 攤平字索引（次序不拘）。
        delta_ms: 位移（正 = 向後、負 = 向前）。

    Returns:
        實際位移（夾到 0 時會細過 ``delta_ms``，甚至 0）。
    """
    chars = iter_flat_chars(line)
    lo, hi = sorted((int(i_from), int(i_to)))
    if lo < 0 or hi >= len(chars) or hi < lo or not delta_ms:
        return 0
    block = chars[lo:hi + 1]
    earliest = min(int(c["begin_ms"]) for _w, _c, c in block)
    delta = max(int(delta_ms), -earliest)
    if delta == 0:
        return 0
    for _w, _c, c in block:
        c["begin_ms"] = int(c["begin_ms"]) + delta
        c["end_ms"] = int(c["end_ms"]) + delta
    sync_word_bounds(line)
    return delta


def repair_plan(line: dict) -> dict[str, Any]:
    """診斷本行時長但**唔改資料**。

    判斷次序：

    1. ``mode="line"`` —— 全行崩壞：最長字 > ``max(6 × 中位數, 1500ms)``。
       漏字段的特徵係「一堆 100ms 字 + 一個 10 秒字」，逐字判斷捉唔到，
       只有整行重攤救得返。
    2. ``mode="spans"`` —— 局部崩壞：連續 ≥1 個零時長（``end - begin ≤ 1``）字。
    3. ``mode="none"`` —— 冇事。

    Returns:
        ``{"mode", "count", "median_ms", "longest_ms", ...}``（``line`` 模式
        另帶 ``begin`` / ``end``；``spans`` 模式另帶 ``spans``）。
    """
    chars = iter_flat_chars(line)
    if len(chars) < 3:
        return {"mode": "none", "count": 0}
    durs = [int(c["end_ms"]) - int(c["begin_ms"]) for _w, _ci, c in chars]
    median = float(statistics.median(durs))
    longest = max(durs)
    if longest > max(REPAIR_STRETCH_RATIO * median, REPAIR_STRETCH_FLOOR_MS):
        return {"mode": "line", "from": 0, "to": len(chars) - 1,
                "count": len(chars), "median_ms": round(median, 1),
                "longest_ms": longest,
                "begin": int(chars[0][2]["begin_ms"]),
                "end": int(chars[-1][2]["end_ms"])}
    runs: list[tuple[int, int]] = []
    i = 0
    while i < len(chars):
        if durs[i] <= 1:
            j = i
            while j + 1 < len(chars) and durs[j + 1] <= 1:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    return {"mode": "spans" if runs else "none", "spans": runs,
            "count": sum(j - i + 1 for i, j in runs),
            "median_ms": round(median, 1), "longest_ms": longest}


def repair_line(line: dict) -> dict[str, Any]:
    """一鍵修補本行時長；回傳實際做咗咩（面板顯示用）。

    見 :func:`repair_plan` 的判斷規則；``mode="line"`` 會連端點一齊重攤
    （因為人手量度出嚟的尾字 end 正是最好的錨），``mode="spans"`` 只補
    零時長段、段外時間一律唔郁。

    Returns:
        ``{"mode": "line"|"spans"|"none", "count", ...}``。
    """
    plan = repair_plan(line)
    mode = plan["mode"]
    if mode == "line":
        done = redistribute_span(line, 0, plan["to"], keep_ends=False)
        if done is None:
            return {"mode": "none", "count": 0}
        return {**done, "mode": "line", "median_ms": plan["median_ms"],
                "longest_ms": plan["longest_ms"]}
    if mode != "spans":
        return {"mode": "none", "count": 0}

    chars = iter_flat_chars(line)
    fixed = 0
    touched: set[int] = set()
    for i, j in plan["spans"]:
        if j > i:
            if redistribute_span(line, i, j, keep_ends=False) is not None:
                fixed += j - i + 1
                touched.update(chars[k][0] for k in range(i, j + 1))
            continue
        # 單一個零時長字：兩邊夾得出空隙就填滿，冇空隙就補 1ms
        cur = chars[i][2]
        prev_end = (int(chars[i - 1][2]["end_ms"]) if i > 0
                    else int(cur["begin_ms"]))
        next_begin = (int(chars[i + 1][2]["begin_ms"]) if i + 1 < len(chars)
                      else int(cur["end_ms"]))
        begin = max(prev_end, int(cur["begin_ms"]))
        end = max(begin + 1, next_begin)
        cur["begin_ms"], cur["end_ms"] = begin, end
        fixed += 1
        touched.add(chars[i][0])
    sync_word_bounds(line)
    for wi in touched:
        line["words"][wi]["estimated"] = True
    return {"mode": "spans", "count": fixed, "median_ms": plan["median_ms"]}


# ---------------------------------------------------------------------------
# 正規化（§7.2 N1–N7，儲存前必須跑）
# ---------------------------------------------------------------------------

def normalize(doc: dict) -> None:
    """N1–N7（in-place）。重疊 clip、倒轉夾返、text 重建、end_ms + hold_ms。"""
    materialize_punct_spaces(doc)      # 標點空格實體化（idempotent）
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
