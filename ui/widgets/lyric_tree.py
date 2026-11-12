# -*- coding: utf-8 -*-
"""lyric_tree — Treeview 歌詞樹（計劃書 §6.1；版面依據 02_編輯器.html）。

一句一行；估算字行前有橙點；雙擊改字（C1 表格改字）。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.messagebox as messagebox
from typing import Callable

import ttkbootstrap as ttkb

from .. import theme, timefmt


class LyricTree(ttkb.Frame):
    """歌詞樹：# / 歌詞 / start / end / 估算 四欄。"""

    COLUMNS = ("num", "text", "start", "end", "est")

    def __init__(self, master, state, *,
                 on_select_line: Callable[[int], None] | None = None,
                 on_play_line: Callable[[int], None] | None = None,
                 on_add_line: Callable[[], None] | None = None,
                 on_insert_line_at: Callable[[int], None] | None = None,
                 on_delete_line: Callable[[int], None] | None = None,
                 on_edit_line: Callable[[int], None] | None = None) -> None:
        super().__init__(master, bootstyle="light")
        self._state = state
        self._on_select_line = on_select_line
        self._on_play_line = on_play_line
        self._on_add_line = on_add_line
        self._on_insert_line_at = on_insert_line_at
        self._on_delete_line = on_delete_line
        self._on_edit_line = on_edit_line
        self._building = False

        mono = theme.mono_font_family()
        self._tree = ttkb.Treeview(
            self, columns=self.COLUMNS, show="headings", selectmode="extended",
            bootstyle="light")
        # 用戶需求：5 欄總寬 = 370px（32+174+66+66+32）。start/end 收到 66 —
        # 5 位數時間碼用等寬 10pt 只需 ~46px；估算 32。慳返嘅位全部畀「歌詞」。
        # 2026-11-11 用戶需求 #3：start/end 改中文標題，值顯示時間碼（1:08.765）
        heads = {"num": ("#", 32, "e"), "text": ("歌詞", 164, "w"),
                 "start": ("開始", 72, "e"), "end": ("結束", 72, "e"),
                 "est": ("估算", 30, "center")}
        for key, (label, width, anchor) in heads.items():
            # 標題跟欄位 anchor（同逐字微調面板一致）：右對齊數字欄如果標題置中，
            # 標題會浮喺數字左邊，睇落唔知屬邊欄
            self._tree.heading(key, text=label, anchor=anchor)
            self._tree.column(key, width=width, anchor=anchor, stretch=(key == "text"))
        self._apply_tag_colors()
        vsb = ttkb.Scrollbar(self, orient="vertical", command=self._tree.yview,
                             bootstyle="secondary-round")
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Double-1>", self._on_double_click)
        # 右鍵選單（2026-11-11 用戶需求 #3）：無字幕區段插句／任意刪句都要有路徑
        self._tree.bind("<Button-3>", self._on_right_click)
        self._menu = tk.Menu(self, tearoff=False)

        # ＋ 新增歌詞行（虛線框效果用 outline bootstyle）
        ttkb.Button(self, text="+ 新增歌詞行（時間軸拖曳範圍／游標位置）",
                    bootstyle="secondary-outline", takefocus=False,
                    command=self._add_line_tail).pack(fill="x", pady=(2, 0))

        state.add_observer(self._on_state_change)
        self.refresh()

    # ------------------------------------------------------------------

    def _apply_tag_colors(self) -> None:
        """套用 tag 顏色（估算字橙、當前行底色）—— 換主題時要重跑。"""
        self._tree.tag_configure("estimated", foreground=theme.WARNING)
        self._tree.tag_configure("current", background=theme.BG_HOVER)

    def restyle(self) -> None:
        """換主題後重新上色（ttk 元件靠 style；tag 色係 per-widget 要自己補）。"""
        self._apply_tag_colors()

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
                i + 1, text,
                timefmt.fmt_ms(int(line.get("start_ms", 0))),
                timefmt.fmt_ms(int(line.get("end_ms", 0))),
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

    def _on_right_click(self, event) -> None:
        """右鍵：先選中該行，再彈選單（修改／前後插行／刪行）。"""
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        line_i = int(iid)
        self._building = True
        try:
            self._tree.selection_set(iid)
        finally:
            self._building = False
        self._state.set_current_line(line_i)

        lines = self._state.lines
        text = (lines[line_i].get("text") or "") if line_i < len(lines) else ""
        self._menu.delete(0, "end")
        self._menu.add_command(
            label=f"✏ 修改第 {line_i + 1} 行歌詞",
            command=lambda: self._call(self._on_edit_line, line_i))
        self._menu.add_separator()
        self._menu.add_command(
            label="+ 喺此行之前插入新行",
            command=lambda: self._call(self._on_insert_line_at, line_i))
        self._menu.add_command(
            label="+ 喺此行之後插入新行",
            command=lambda: self._call(self._on_insert_line_at, line_i + 1))
        self._menu.add_separator()
        self._menu.add_command(
            label=f"🗑 刪除此行（{text[:12]}{'…' if len(text) > 12 else ''}）",
            command=lambda: self._call(self._on_delete_line, line_i))
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    @staticmethod
    def _call(fn, arg) -> None:
        """叫 callback（未接線就靜靜唔做，唔會爆）。"""
        if fn is not None:
            fn(arg)

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

    btns = ttkb.Frame(dlg, bootstyle="light")
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
