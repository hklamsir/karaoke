# -*- coding: utf-8 -*-
"""markdown_view — 極簡 markdown → tkinter.Text 渲染器（計劃書 §5.5.3）。

支援子集：# ~ #### 標題 / | 表格（等寬網格，**east-asian width 對齊** R16）/
``` 圍欄碼塊 / - 1. 清單 / **粗體** / `行內碼` / [文字](url) 連結（R19：手動 bind）。
唔引入 markdown 套件。
"""

from __future__ import annotations

import re
import tkinter as tk
import unicodedata
import webbrowser
from typing import Callable

from .. import theme

_TABLE_SPLIT = re.compile(r"\s*\|\s*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def display_width(text: str) -> int:
    """East-asian display width（R16：F/W = 2 欄，唔好用 len()）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "FW" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


class MarkdownView(tk.Text):
    """只讀 Text + markdown tag 渲染。"""

    def __init__(self, master, *, height: int = 20) -> None:
        super().__init__(master, wrap="word", height=height, state="disabled",
                         bg=theme.BG_PANEL, fg=theme.FG_MAIN, relief="flat",
                         padx=12, pady=10, insertwidth=0,
                         font=(theme.ui_font_family(), 12))
        mono = theme.mono_font_family()
        ui = theme.ui_font_family()
        # 用戶需求 #4：幫助頁文字放大 2 級（原 base 10 → 12）
        for tag, cfg in {
            "h1": ({"font": (ui, 17, "bold"), "foreground": theme.ACCENT,
                    "spacing1": 12, "spacing3": 5}),
            "h2": ({"font": (ui, 15, "bold"), "foreground": theme.ACCENT,
                    "spacing1": 10, "spacing3": 4}),
            "h3": ({"font": (ui, 13, "bold"), "foreground": theme.FG_MAIN,
                    "spacing1": 8}),
            "h4": ({"font": (ui, 12, "bold"), "foreground": theme.FG_DIM}),
            "code": ({"font": (mono, 11), "background": theme.BG_DEEP,
                      "foreground": "#9CDCFE"}),
            "bold": ({"font": (ui, 12, "bold")}),
            "link": ({"foreground": theme.ACCENT, "underline": True}),
            "listitem": ({"lmargin1": 16, "lmargin2": 24}),
            "sep": ({"foreground": theme.BORDER}),
        }.items():
            self.tag_configure(tag, **cfg)
        # 碼塊左邊框 + 底色（§5.5.3）
        self.tag_configure("codeblock", font=(mono, 11), background=theme.BG_DEEP,
                           foreground=theme.FG_MAIN, lmargin1=12, lmargin2=12,
                           spacing1=2, spacing3=2)
        self.tag_bind("link", "<Enter>",
                      lambda _e: self.configure(cursor="hand2"))
        self.tag_bind("link", "<Leave>",
                      lambda _e: self.configure(cursor=""))
        self._links: list[tuple[str, str]] = []     # (start_index_pattern, url)
        self.tag_bind("link", "<Button-1>", self._on_link_click)

    # ------------------------------------------------------------------

    def render(self, markdown: str) -> None:
        """整段 markdown → Text。重複呼叫會 append。"""
        self.configure(state="normal")
        lines = markdown.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("```"):
                i = self._render_codeblock(lines, i + 1)
                continue
            if re.match(r"^#{1,4}\s", line):
                level = len(line) - len(line.lstrip("#"))
                self._inline(line.lstrip("#").strip(), f"h{level}")
                self.insert("end", "\n")
                i += 1
                continue
            if line.strip().startswith("|") and i + 1 < len(lines) \
                    and set(lines[i + 1].replace("|", "").replace(" ", "")) <= {"-", ":"} \
                    and lines[i + 1].strip().startswith("|"):
                i = self._render_table(lines, i)
                continue
            if re.match(r"^\s*[-*]\s", line):
                self._inline("• " + re.sub(r"^\s*[-*]\s", "", line), "listitem")
                self.insert("end", "\n")
                i += 1
                continue
            m = re.match(r"^\s*(\d+)\.\s(.*)", line)
            if m:
                self._inline(f"{m.group(1)}. {m.group(2)}", "listitem")
                self.insert("end", "\n")
                i += 1
                continue
            if line.strip() == "":
                self.insert("end", "\n")
                i += 1
                continue
            self._inline(line)
            self.insert("end", "\n")
            i += 1
        self.configure(state="disabled")

    def _render_codeblock(self, lines: list[str], i: int) -> int:
        while i < len(lines) and not lines[i].startswith("```"):
            self.insert("end", lines[i] + "\n", "codeblock")
            i += 1
        return i + 1

    def _render_table(self, lines: list[str], i: int) -> int:
        """表格 → 等寬網格（display width 對齊；R16）。"""
        rows: list[list[str]] = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            cells = [c.strip() for c in _TABLE_SPLIT.split(lines[i].strip().strip("|"))]
            if not all(set(c) <= {"-", ":"} for c in cells):
                rows.append(cells)
            i += 1
        if not rows:
            return i
        n_cols = max(len(r) for r in rows)
        widths = [max(display_width(r[c]) if c < len(r) else 0 for r in rows)
                  for c in range(n_cols)]
        for ri, row in enumerate(rows):
            text = "  ".join(pad(row[c] if c < len(row) else "", widths[c])
                             for c in range(n_cols))
            tag = "sep" if ri == 0 else ()
            self._inline(text, tag) if tag else self._inline(text)
            self.insert("end", "\n")
        self.insert("end", "\n")
        return i

    def _inline(self, text: str, base_tag: str | tuple = ()) -> None:
        """**粗體** / `行內碼` / [link](url) 行內解析。"""
        if isinstance(base_tag, str):
            base_tag = (base_tag,)
        pos = 0
        pattern = re.compile(r"(\*\*(?P<bold>.+?)\*\*|`(?P<code>[^`]+)`"
                             r"|\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)]+)\))")
        for m in pattern.finditer(text):
            if m.start() > pos:
                self.insert("end", text[pos:m.start()], base_tag)
            if m.group("bold"):
                self.insert("end", m.group("bold"), base_tag + ("bold",))
            elif m.group("code"):
                self.insert("end", m.group("code"), base_tag + ("code",))
            elif m.group("ltext"):
                start = self.index("end-1c")
                self.insert("end", m.group("ltext"), base_tag + ("link",))
                self._links.append((start, m.group("lurl")))
            pos = m.end()
        if pos < len(text):
            self.insert("end", text[pos:], base_tag)

    def _on_link_click(self, event) -> None:
        # R19：Text 唔支援點擊連結 → 座標 → index → 映射返連結清單
        idx = self.index(f"@{event.x},{event.y}")
        for start, url in self._links:
            end = f"{start}+{len(url_display(self, start))}c" if False else None
            # 簡化：點中 link tag 就開最接近嘅連結（正常一頁得幾條）
        try:
            tags = self.tag_names(idx)
        except Exception:   # noqa: BLE001
            return
        if "link" in tags:
            nearest = min(self._links, key=lambda s: abs(
                int(self.index(s[0]).split(".")[0]) - int(idx.split(".")[0])))
            webbrowser.open(nearest[1])


def url_display(_view, _start) -> str:      # pragma: no cover — 保留 API
    return ""
