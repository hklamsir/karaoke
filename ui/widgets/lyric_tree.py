# -*- coding: utf-8 -*-
"""lyric_tree — Treeview 歌詞樹（計劃書 §6.1；版面依據 02_編輯器.html）。

一句一行；估算字行前有橙點；雙擊改字（C1 表格改字）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as messagebox
from typing import Callable

import ttkbootstrap as ttkb

from .. import theme


class LyricTree(ttkb.Frame):
    """歌詞樹：# / 歌詞 / start / end / 估算 四欄。"""

    COLUMNS = ("num", "text", "start", "end", "est")

    def __init__(self, master, state, *,
                 on_select_line: Callable[[int], None] | None = None) -> None:
        super().__init__(master, bootstyle="dark")
        self._state = state
        self._on_select_line = on_select_line
        self._building = False

        mono = theme.mono_font_family()
        self._tree = ttkb.Treeview(
            self, columns=self.COLUMNS, show="headings", selectmode="extended",
            bootstyle="dark")
        heads = {"num": ("#", 36, "e"), "text": ("歌詞", 150, "w"),
                 "start": ("start", 72, "e"), "end": ("end", 72, "e"),
                 "est": ("估算", 40, "center")}
        for key, (label, width, anchor) in heads.items():
            self._tree.heading(key, text=label)
            self._tree.column(key, width=width, anchor=anchor, stretch=(key == "text"))
        self._tree.tag_configure("estimated", foreground=theme.WARNING)
        self._tree.tag_configure("current", background=theme.BG_HOVER)
        vsb = ttkb.Scrollbar(self, orient="vertical", command=self._tree.yview,
                             bootstyle="secondary-round")
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", self._on_double_click)

        # ＋ 新增歌詞行（虛線框效果用 outline bootstyle）
        ttkb.Button(self, text="＋ 新增歌詞行（喺時間軸拖曳範圍，或按此加喺結尾）",
                    bootstyle="secondary-outline", takefocus=False,
                    command=self._add_line_tail).pack(fill="x", pady=(2, 0))

        state.add_observer(self._on_state_change)
        self.refresh()

    # ------------------------------------------------------------------

    def _on_state_change(self, reason: str) -> None:
        if reason in ("load", "edit", "save", "filter"):
            self.refresh()
        elif reason in ("select",):
            self._highlight_current()

    def refresh(self) -> None:
        self._building = True
        self._tree.delete(*self._tree.get_children())
        # 篩選模式（§4.4）：只列有估算字嘅行（iid 保留原始索引）
        for i, line in self._state.visible_lines():
            est_count = sum(1 for w in line.get("words") or [] if w.get("estimated"))
            text = line.get("text") or ""
            tags = []
            if est_count:
                tags.append("estimated")
            if i == self._state.current_line_i:
                tags.append("current")
            self._tree.insert("", "end", iid=str(i), values=(
                i + 1, text, line.get("start_ms", 0), line.get("end_ms", 0),
                f"●{est_count}" if est_count else ""), tags=tags)
        self._building = False
        self._highlight_current()

    def _highlight_current(self) -> None:
        i = str(self._state.current_line_i)
        if self._tree.exists(i):
            self._tree.see(i)
            # 唔搶走用戶嘅多選（selectmode extended 畀合併用）
            if not self._tree.selection():
                self._tree.selection_set(i)

    # ------------------------------------------------------------------

    def selected_lines(self) -> list[int]:
        """多選行（合併用），升冪。"""
        return sorted(int(s) for s in self._tree.selection())

    def _on_tree_select(self, _event=None) -> None:
        if self._building:
            return
        sel = self.selected_lines()
        if sel:
            self._state.set_current_line(sel[0])
            if self._on_select_line:
                self._on_select_line(sel[0])

    def _on_double_click(self, event) -> None:
        """雙擊改字（C1）：彈輸入框 → SetLineText command。"""
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        line_i = int(iid)
        line = self._state.lines[line_i]
        current = line.get("text") or ""
        from ..commands import SetLineText
        new = _ask_text(self, "改歌詞", "新歌詞（字數唔同會重建時間軸並標估算）：", current)
        if new is not None and new != current:
            self._state.push(SetLineText(line_i, new))

    def _add_line_tail(self) -> None:
        last_end = 0
        if self._state.lines:
            last_end = int(self._state.lines[-1].get("end_ms", 0))
        from ..commands import AddLine
        new = _ask_text(self, "新增歌詞行", "歌詞（時間：尾行後 +3 秒，之後喺時間軸校）：")
        if new:
            self._state.push(AddLine(last_end + 500, last_end + 3500, new))


def _ask_text(parent, title: str, prompt: str, initial: str = "") -> str | None:
    """深色主題下嘅簡單文字輸入對話框（simpledialog 白底唔襯）。"""
    import ttkbootstrap as ttkb
    dlg = ttkb.Toplevel(title=title, size=(380, 150))
    dlg.transient(parent.winfo_toplevel())
    dlg.grab_set()
    result: list[str | None] = [None]

    ttkb.Label(dlg, text=prompt, wraplength=340, bootstyle="secondary").pack(
        padx=12, pady=(12, 4), anchor="w")
    var = tk.StringVar(value=initial)
    entry = ttkb.Entry(dlg, textvariable=var)
    entry.pack(fill="x", padx=12)
    entry.focus_set()
    entry.select_range(0, "end")

    def ok(_e=None) -> None:
        result[0] = var.get().strip()
        dlg.destroy()

    def cancel(_e=None) -> None:
        dlg.destroy()

    btns = ttkb.Frame(dlg, bootstyle="dark")
    btns.pack(fill="x", padx=12, pady=10)
    ttkb.Button(btns, text="確定", bootstyle=theme.ACCENT, command=ok).pack(side="right", padx=4)
    ttkb.Button(btns, text="取消", bootstyle="secondary", command=cancel).pack(side="right")
    dlg.bind("<Return>", ok)
    dlg.bind("<Escape>", cancel)
    dlg.wait_window()
    return result[0]
