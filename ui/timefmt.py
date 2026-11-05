# -*- coding: utf-8 -*-
"""timefmt — 時間碼 ⇄ 毫秒 的純函數（無 Tk、無 I/O，可直接單測）。

點解要獨立一個模組：逐字微調面板以前直接顯示裸毫秒（``221995``），用戶要
自己換算「呢個字喺曲中第幾分幾秒」，改一個字要開計算機。格式化與解析必須
**對稱**（畫面顯示咩就打得返咩），所以兩邊放埋一齊、一齊測。

顯示格式（用戶 2026-11-03 拍板）：``3:41.995`` = m:ss.mmm；片長過一小時
自動變 ``1:03:41.995``。
"""

from __future__ import annotations

import re


class TimeParseError(ValueError):
    """時間碼無法解析。``str(exc)`` 直接顯示畀用戶睇。"""


_NON_COLON = re.compile(r"[\s,，]")


def fmt_ms(ms: int) -> str:
    """毫秒 → 時間碼。

    Args:
        ms: 毫秒（負數當 0）。

    Returns:
        ``m:ss.mmm``（少於一小時）或 ``h:mm:ss.mmm``。

    Examples:
        >>> fmt_ms(221995)
        '3:41.995'
        >>> fmt_ms(3_821_995)
        '1:03:41.995'
    """
    ms = max(0, int(round(ms)))
    hours, rest = divmod(ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, msec = divmod(rest, 1000)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{msec:03d}"
    return f"{minutes}:{seconds:02d}.{msec:03d}"


def fmt_span(begin_ms: int, end_ms: int) -> str:
    """``3:42.958 → 3:53.158（10.200s）`` —— 狀態提示用，一眼睇到攤咗幾長。"""
    dur = max(0, int(end_ms) - int(begin_ms))
    return f"{fmt_ms(begin_ms)} → {fmt_ms(end_ms)}（{dur / 1000:.3f}s）"


def parse_time(text: str) -> int:
    """時間碼 → 毫秒。接受人手會打嘅幾種寫法。

    規則（刻意保留「純整數＝毫秒」，唔想改變舊用戶肌肉記憶）：

    - ``3:41.995`` / ``3:41`` / ``1:03:41.995`` → 冒號 = 時:分:秒（可省略時）
    - ``221.995`` → 有小數點但無冒號 = 秒
    - ``221995`` → 純整數 = **毫秒**（同舊版 UI 一致）
    - 全形冒號 ``：``、空格、逗號會先清走

    Args:
        text: 用戶輸入。

    Returns:
        毫秒（非負整數）。

    Raises:
        TimeParseError: 空白、負數、格式唔啱、秒數 >= 60。
    """
    raw = _NON_COLON.sub("", (text or "").replace("：", ":"))
    if not raw:
        raise TimeParseError("時間唔可以留空。")
    if raw.startswith("-"):
        raise TimeParseError("時間唔可以係負數。")

    if ":" in raw:
        parts = raw.split(":")
        if len(parts) > 3:
            raise TimeParseError(f"「{text}」格式唔啱：最多 時:分:秒.毫秒。")
        try:
            nums = [float(p) for p in parts]
        except ValueError as exc:
            raise TimeParseError(f"「{text}」格式唔啱：只可以填數字同冒號。") from exc
        if any(n < 0 for n in nums):
            raise TimeParseError("時間唔可以係負數。")
        if len(parts) == 3:
            hours, minutes, seconds = nums
            if minutes >= 60:
                raise TimeParseError(f"「{text}」分秒唔啱：分鐘要少於 60。")
        elif len(parts) == 2:
            hours = 0.0
            minutes, seconds = nums
        else:
            raise TimeParseError(f"「{text}」格式唔啱：冒號兩邊都要填數字。")
        if seconds >= 60:
            raise TimeParseError(f"「{text}」秒數唔啱：秒要少於 60。")
        total_s = hours * 3600 + minutes * 60 + seconds
    elif "." in raw:
        try:
            total_s = float(raw)
        except ValueError as exc:
            raise TimeParseError(f"「{text}」格式唔啱：只可以填數字同小數點。") from exc
        if total_s < 0:
            raise TimeParseError("時間唔可以係負數。")
    else:
        if not raw.isdigit():
            raise TimeParseError(f"「{text}」格式唔啱：只可以填數字。")
        return int(raw)      # 純整數 = 毫秒
    return int(round(total_s * 1000))
