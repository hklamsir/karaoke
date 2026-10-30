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
                 on_select_line: Callable[[int], None] | None = None,
                 on_play_line: Callable[[int], None] | None = None,
                 on_add_line: Callable[[], None] | None = None) -> None:
        super().__init__(master, bootstyle="dark")
        self._state = state
        self._on_select_line = on_select_line
        self._on_play_line = on_play_line
        self._on_add_line = on_add_line
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
        elif reason == "follow":
            # 播放中游標行變咗 → **反白**當前句（用戶需求 #2：強制跟）
            self._highlight_current(force=True)
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

    def _highlight_current(self, force: bool = False) -> None:
        i = str(self._state.current_line_i)
        if self._tree.exists(i):
            self._tree.see(i)
            # force（跟播）→ 反白當前句；非 force 就唔搶用戶嘅多選（合併用）
            # ⚠️ selection_set 會觸發 <<TreeviewSelect>> → _on_tree_select → notify
            #    —— 用 _building flag 擋住防循環
            if force or not self._tree.selection():
                self._building = True
                try:
                    self._tree.selection_set(i)
                finally:
                    self._building = False

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
        """雙擊句子 = 跳到該句開始時間播放（預覽 + 時間軸同步）。

        改字改為「點選句子 → 按工具列 ✏ 修改」（用戶指定互動）。
        """
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        line_i = int(iid)
        self._state.set_current_line(line_i)
        if self._on_play_line:
            self._on_play_line(line_i)

    def _add_line_tail(self) -> None:
        # 用戶回報 _ask_text 彈窗冇生效 → 由 editor page 提供 inline 輸入列
        if self._on_add_line is not None:
            self._on_add_line()
            return
        last_end = 0
        if self._state.lines:
            last_end = int(self._state.lines[-1].get("end_ms", 0))
        from ..commands import AddLine
        new = _ask_text(self, "新增歌詞行", "歌詞（時間：尾行後 +3 秒，之後喺時間軸校）：")
        if new:
            self._state.push(AddLine(last_end + 500, last_end + 3500, new))


def _ask_text(parent, title: str, prompt: str, initial: str = "") -> str | None:
    """深色主題下嘅簡單文字輸入對話框（simpledialog 白底唔襯）。

    ⚠️ grab_set 喺視窗未 viewable 之前呼叫會拋 TclError，令 wait_window 後面
    全部跳過（實測：用戶撳確定冇生效）——所以要先 wait_visibility + try/except。
    """
    import ttkbootstrap as ttkb
    dlg = ttkb.Toplevel(title=title, size=(380, 150))
    dlg.transient(parent.winfo_toplevel())
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
    try:
        dlg.wait_visibility()
        dlg.grab_set()
        # ⚠️ focus_set 喺 map 之前呼叫會落咗去主視窗 —— 用戶打字 entry 完全收唔到，
        # 撳確定 = 原文不變（實測「確定冇生效」嘅第二根源）。要 map 之後 focus_force。
        entry.focus_force()
    except tk.TclError:
        pass
    dlg.wait_window()
    return result[0]
