# -*- coding: utf-8 -*-
"""pages.editor — 字幕編輯器（計劃書 §6；版面依據 docs/線框圖/02_編輯器.html）。

C1 雙軌：上歌詞樹（Treeview）／下 Canvas 時間軸／右逐字微調精確 ms —— 三者雙向同步，
全部由 EditorState 單一數據源推導（R22）。
音訊（commit 6）／大 preview 窗（commit 7）／估算字功能（commit 8）／燒錄（commit 9）逐步填入。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from pathlib import Path

import ttkbootstrap as ttkb

from .. import models, state, theme
from ..commands import DeleteLine, InsertWord, MergeLines, SetCharTiming, SplitLine
from ..widgets.lyric_tree import LyricTree, _ask_text
from ..widgets.timeline import TimelineCanvas

FETCH_MS = 100      # ←→ 游標步長
FINE_MS = 10        # Shift ←→
FRAME_MS = 17       # Ctrl ←→ ≈ 1 帧 @60fps


class EditorPage(ttkb.Frame):
    """字幕編輯器。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        self._state = state.EditorState()
        self._audio = None          # commit 6
        self._thumbs = None         # commit 7

        self._build_toolbar()
        body = ttkb.Frame(self, bootstyle="dark")
        body.pack(fill="both", expand=True, pady=(theme.SPACE, 0))

        # --- 左欄：歌詞樹 + 逐字微調 ---
        left = ttkb.Frame(body, bootstyle="dark", width=260)
        left.pack(side="left", fill="y", padx=(0, theme.SPACE))
        left.pack_propagate(False)

        tree_box = ttkb.LabelFrame(left, text="歌詞樹", bootstyle="dark")
        tree_box.pack(fill="both", expand=True)
        self._tree = LyricTree(tree_box, self._state)
        self._tree.pack(fill="both", expand=True, padx=2, pady=2)

        row = ttkb.Frame(left, bootstyle="dark")
        row.pack(fill="x", pady=(2, 0))
        ttkb.Button(row, text="✂ 斷句", bootstyle="secondary", takefocus=False,
                    command=self._split_at_word).pack(side="left", padx=(0, 2))
        ttkb.Button(row, text="🔗 合併", bootstyle="secondary", takefocus=False,
                    command=self._merge_selected).pack(side="left", padx=2)
        ttkb.Button(row, text="🗑 刪行", bootstyle="secondary", takefocus=False,
                    command=self._delete_line).pack(side="left", padx=2)

        self._build_finetune(left)

        # --- 右欄：preview（commit 7）+ 分隔條 + 時間軸 ---
        right = ttkb.Frame(body, bootstyle="dark")
        right.pack(side="left", fill="both", expand=True)

        self._preview_slot = ttkb.LabelFrame(right, text="影片預覽（commit 7 填入）",
                                             bootstyle="dark")
        self._preview_slot.pack(fill="both", expand=True)
        ttkb.Label(self._preview_slot, text="大 preview 窗：16:9 畫面 + 字幕疊圖 + 2fps scrub strip",
                   bootstyle="secondary").pack(expand=True)

        self._separator = tk.Canvas(right, height=8, bg=theme.BG_HOVER,
                                    highlightthickness=0, cursor="sb_v_double_arrow")
        self._separator.pack(fill="x", pady=2)

        tl_box = ttkb.LabelFrame(right, text="時間軸 · 波形", bootstyle="dark")
        tl_box.pack(fill="both", expand=True)
        self._timeline = TimelineCanvas(tl_box, self._state,
                                        on_seek=self._on_seek)
        self._timeline.pack(fill="both", expand=True, padx=2, pady=2)

        # --- 狀態列 ---
        self._build_statusbar()

        self._state.add_observer(self._on_state_change)

    # ------------------------------------------------------------------
    # 工具列
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttkb.Frame(self, height=theme.TOOLBAR_H, bootstyle="dark")
        bar.pack(fill="x")
        self._save_btn = ttkb.Button(bar, text="💾 儲存", bootstyle="secondary",
                                     takefocus=False, command=self.save)
        self._save_btn.pack(side="left", padx=(0, 2))
        self._undo_btn = ttkb.Button(bar, text="⟲", width=3, bootstyle="secondary",
                                     takefocus=False, command=self.undo)
        self._undo_btn.pack(side="left", padx=2)
        self._redo_btn = ttkb.Button(bar, text="⟳", width=3, bootstyle="secondary",
                                     takefocus=False, command=self.redo)
        self._redo_btn.pack(side="left", padx=2)

        ttkb.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self._btn_home = ttkb.Button(bar, text="⏮", width=3, bootstyle="secondary",
                                     takefocus=False, command=lambda: self._jump_line(-1))
        self._btn_home.pack(side="left", padx=2)
        self._play_btn = ttkb.Button(bar, text="▶", width=3, bootstyle="secondary",
                                     takefocus=False, command=self._toggle_play, state="disabled")
        self._play_btn.pack(side="left", padx=2)
        ttkb.Button(bar, text="⏭", width=3, bootstyle="secondary", takefocus=False,
                    command=lambda: self._jump_line(+1)).pack(side="left", padx=2)
        self._replay_btn = ttkb.Button(bar, text="重播3秒", bootstyle="secondary",
                                       takefocus=False, state="disabled",
                                       command=lambda: None)
        self._replay_btn.pack(side="left", padx=2)

        ttkb.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self._est_chip = ttkb.Label(bar, text="◻ 只顯示估算字", padding=(8, 3),
                                    bootstyle="secondary")
        self._est_chip.pack(side="left", padx=2)
        self._overlay_chip = ttkb.Label(bar, text="◻ 字幕疊圖", padding=(8, 3),
                                        bootstyle="secondary")
        self._overlay_chip.pack(side="left", padx=2)

        self._time_var = tk.StringVar(value="00:00.000 / 00:00.000")
        ttkb.Label(bar, textvariable=self._time_var, bootstyle="info",
                   font=(theme.mono_font_family(), 11)).pack(side="right", padx=theme.SPACE)

    def _build_statusbar(self) -> None:
        bar = ttkb.Frame(self, bootstyle="dark")
        bar.pack(fill="x", pady=(2, 0))
        self._status_var = tk.StringVar(value="未開檔")
        ttkb.Label(bar, textvariable=self._status_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 9)).pack(side="left", padx=theme.SPACE)

    # ------------------------------------------------------------------
    # 逐字微調面板（§6.3）
    # ------------------------------------------------------------------

    def _build_finetune(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="逐字微調", bootstyle="dark")
        box.pack(fill="both", pady=(theme.SPACE, 0))

        mono = theme.mono_font_family()
        self._ft_tree = ttkb.Treeview(box, columns=("ch", "word", "begin", "end"),
                                      show="headings", height=6, bootstyle="dark")
        for key, label, width, anchor in (("ch", "字", 36, "center"),
                                          ("word", "詞", 64, "w"),
                                          ("begin", "begin", 64, "e"),
                                          ("end", "end", 64, "e")):
            self._ft_tree.heading(key, text=label)
            self._ft_tree.column(key, width=width, anchor=anchor)
        self._ft_tree.tag_configure("estimated", foreground=theme.WARNING)
        self._ft_tree.pack(fill="both", expand=True, padx=2, pady=2)
        self._ft_tree.bind("<<TreeviewSelect>>", self._on_ft_select)

        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=2, pady=2)
        ttkb.Label(row, text="begin", bootstyle="secondary",
                   font=(mono, 9)).pack(side="left")
        self._begin_var = tk.StringVar(value="0")
        ttkb.Spinbox(row, textvariable=self._begin_var, from_=0, to=10_000_000,
                     increment=10, width=8,
                     font=(mono, 9)).pack(side="left", padx=(2, theme.SPACE))
        ttkb.Label(row, text="end", bootstyle="secondary",
                   font=(mono, 9)).pack(side="left")
        self._end_var = tk.StringVar(value="0")
        ttkb.Spinbox(row, textvariable=self._end_var, from_=0, to=10_000_000,
                     increment=10, width=8,
                     font=(mono, 9)).pack(side="left", padx=2)
        ttkb.Button(row, text="套用", bootstyle="secondary", takefocus=False,
                    command=self._apply_ft).pack(side="left", padx=theme.SPACE)

        row2 = ttkb.Frame(box, bootstyle="dark")
        row2.pack(fill="x", padx=2, pady=(0, 2))
        ttkb.Button(row2, text="✂ 斷句（選中詞後）", bootstyle="secondary", takefocus=False,
                    command=self._split_at_word).pack(side="left", padx=(0, 2))
        ttkb.Button(row2, text="＋ 插入字", bootstyle="secondary", takefocus=False,
                    command=self._insert_char).pack(side="left", padx=2)
    # ------------------------------------------------------------------
    # 開檔 / 存檔（§5.3 / E2）
    # ------------------------------------------------------------------

    def load_timings(self, path: str | Path) -> None:
        """主頁 / 快捷鍵入口：讀 timings.json + 驗證原片存在（R8）。"""
        try:
            self._state.load(path)
        except Exception as exc:   # noqa: BLE001
            messagebox.showerror("開檔失敗", f"{path}\n\n{exc}")
            return
        video = self._state.video_path
        if video and not video.is_file():
            if messagebox.askyesno("原片已移動",
                                   f"原片搵唔到：\n{video}\n\n重新指定原片？（燒錄要用）"):
                new = filedialog.askopenfilename(
                    title="重新指定原片",
                    filetypes=[("影片檔", "*.mp4 *.mkv *.mov *.webm *.avi"), ("全部檔案", "*.*")])
                if new:
                    self._state.reassign_video(new)

    def save(self) -> None:
        if self._state.path is None:
            messagebox.showinfo("未開檔", "先喺主頁開一份 *_timings.json。")
            return
        try:
            self._state.save()
        except Exception as exc:   # noqa: BLE001
            messagebox.showerror("儲存失敗", str(exc))
            return
        self._set_status_dirty()

    def _confirm_discard(self) -> bool:
        """關視窗 / 切檔前提示未儲存（E2）。"""
        if not self._state.dirty:
            return True
        return messagebox.askyesno("未儲存", "有未儲存嘅修改，照樣離開？（唔會自動儲存）")

    # ------------------------------------------------------------------
    # undo / redo / transport
    # ------------------------------------------------------------------

    def undo(self) -> None:
        if not self._state.undo():
            self.winfo_toplevel().bell()

    def redo(self) -> None:
        if not self._state.redo():
            self.winfo_toplevel().bell()

    def _toggle_play(self) -> None:
        pass   # commit 6（pygame）

    def _jump_line(self, direction: int) -> None:
        lines = self._state.lines
        if not lines:
            return
        target = max(0, min(len(lines) - 1, self._state.current_line_i + direction))
        self._state.set_current_line(target)
        ms = int(lines[target].get("start_ms", 0))
        self._state.set_cursor(ms)
        self._on_seek(ms)

    def _on_seek(self, ms: int) -> None:
        """時間軸 seek：音訊播放同步由 commit 6 接手（而家淨係移游標）。"""
        if self._audio is not None:
            self._audio.seek(ms)

    # ------------------------------------------------------------------
    # 結構操作（斷句 / 合併 / 刪行 / 插入字）
    # ------------------------------------------------------------------

    def _split_at_word(self) -> None:
        ref = self._state.selected_word
        if ref is None:
            messagebox.showinfo("斷句", "先喺歌詞樹 / 時間軸選中一個字（喺佢所屬詞之後斷開）。")
            return
        line_i, word_i = ref
        line = self._state.lines[line_i]
        words = line.get("words") or []
        # 喺「選中字所屬詞」之後斷：如果選中字唔係詞尾，斷喺詞尾
        target_word_i = word_i
        if 0 <= word_i < len(words) and word_i >= len(words) - 1:
            messagebox.showinfo("斷句", "選中嘅係最後一個詞，後面冇嘢可以斷。")
            return
        self._state.push(SplitLine(line_i, target_word_i + 1))

    def _merge_selected(self) -> None:
        sel = self._tree.selected_lines()
        if len(sel) != 2 or sel[1] - sel[0] != 1:
            messagebox.showinfo("合併", "揀住相鄰兩行（Ctrl+click 多選）再合併。")
            return
        self._state.push(MergeLines(sel[0], 2))

    def _delete_line(self) -> None:
        sel = self._tree.selected_lines()
        if not sel:
            messagebox.showinfo("刪行", "先揀一行。")
            return
        for line_i in reversed(sel):
            self._state.push(DeleteLine(line_i))
    def _insert_char(self) -> None:
        ref = self._state.selected
        if ref is None:
            messagebox.showinfo("插入字", "先選中一個字（新字會插喺佢前面）。")
            return
        from ..widgets.lyric_tree import _ask_text
        text = _ask_text(self, "插入字", "要插入嘅字（原字時間軸不變，唔會推遞）：")
        if text and len(text) == 1:
            line_i, word_i, _char_i = ref
            self._state.push(InsertWord(line_i, word_i, text))

    # ------------------------------------------------------------------
    # 逐字微調
    # ------------------------------------------------------------------

    def _on_state_change(self, reason: str) -> None:
        if reason in ("load", "edit", "save", "select"):
            self._refresh_finetune()
        self._refresh_buttons()
        self._refresh_status()
        self._update_time_display()

    def _refresh_finetune(self) -> None:
        st = self._state
        self._ft_tree.delete(*self._ft_tree.get_children())
        if not st.lines:
            return
        line_i = st.clamp_line_i(st.current_line_i)
        line = st.lines[line_i]
        for word_i, word in enumerate(line.get("words") or []):
            for char_i, char in enumerate(word.get("chars") or []):
                self._ft_tree.insert("", "end", iid=f"{line_i},{word_i},{char_i}", values=(
                    char.get("text", ""), word.get("text", ""),
                    char.get("begin_ms", 0), char.get("end_ms", 0)),
                    tags=("estimated",) if word.get("estimated") else ())
        sel = st.selected
        if sel is not None and sel[0] == line_i:
            iid = ",".join(map(str, sel))
            if self._ft_tree.exists(iid):
                self._ft_tree.selection_set(iid)
                self._ft_tree.see(iid)
        self._sync_ft_spinboxes()

    def _sync_ft_spinboxes(self) -> None:
        sel = self._state.selected
        if sel is None:
            self._begin_var.set("—")
            self._end_var.set("—")
            return
        line = self._state.lines[sel[0]] if sel[0] < len(self._state.lines) else None
        char = models.char_of(line, sel[1], sel[2]) if line else None
        if char:
            self._begin_var.set(str(char["begin_ms"]))
            self._end_var.set(str(char["end_ms"]))

    def _on_ft_select(self, _event=None) -> None:
        sel = self._ft_tree.selection()
        if not sel:
            return
        line_i, word_i, char_i = map(int, sel[0].split(","))
        self._state.set_selection((line_i, word_i, char_i))

    def _apply_ft(self) -> None:
        """精確 ms 輸入（C1 三軌之一）：Spinbox 數值 → SetCharTiming。"""
        sel = self._state.selected
        if sel is None:
            messagebox.showinfo("逐字微調", "先喺時間軸 / 面板選中一個字。")
            return
        try:
            begin, end = int(self._begin_var.get()), int(self._end_var.get())
        except ValueError:
            messagebox.showwarning("格式錯誤", "begin / end 要係整數 ms。")
            return
        if end <= begin:
            messagebox.showwarning("格式錯誤", "end 要大過 begin。")
            return
        line = self._state.lines[sel[0]]
        char = models.char_of(line, sel[1], sel[2])
        if char is None:
            return
        old = (int(char["begin_ms"]), int(char["end_ms"]))
        if (begin, end) != old:
            self._state.push(SetCharTiming(sel[0], sel[1], sel[2], old, (begin, end)))

    # ------------------------------------------------------------------
    # 狀態顯示
    # ------------------------------------------------------------------

    def _refresh_buttons(self) -> None:
        self._undo_btn.configure(
            state="normal" if self._state.undo_stack.can_undo() else "disabled")
        self._redo_btn.configure(
            state="normal" if self._state.undo_stack.can_redo() else "disabled")

    def _refresh_status(self) -> None:
        st = self._state
        if st.path is None:
            self._status_var.set("未開檔")
            return
        s = st.stats()
        est_pct = f"{s['estimated_ratio'] * 100:.0f}%" if s["estimated_total_words"] else "—"
        dirty = "●未儲存" if st.dirty else "已儲存"
        total = models.total_duration_ms(st.doc)
        self._status_var.set(
            f"游標 {_fmt_ms(st.cursor_ms)}/{_fmt_ms(total)} │ "
            f"{s['num_lines']}行·{s['num_chars']}字 │ ●估算 {s['estimated_words']}詞 {est_pct} │ {dirty} │ "
            f"{st.path.name}")

    def _set_status_dirty(self) -> None:
        self._refresh_status()

    def _update_time_display(self) -> None:
        st = self._state
        total = models.total_duration_ms(st.doc)
        self._time_var.set(f"{_fmt_ms(st.cursor_ms)} / {_fmt_ms(total)}")

    # ------------------------------------------------------------------
    # 快捷鍵（app 轉發）
    # ------------------------------------------------------------------

    def on_cursor_step(self, delta_ms: int) -> None:
        self._state.set_cursor(self._state.cursor_ms + delta_ms)

    def on_close_request(self) -> bool:
        """app 關窗前問；True = 可以關。"""
        return self._confirm_discard()

    def on_show(self) -> None:
        self._refresh_status()
        self._refresh_finetune()

    def on_hide(self) -> None:
        pass


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    minutes, rest = divmod(ms, 60_000)
    seconds, msec = divmod(rest, 1000)
    return f"{minutes:02d}:{seconds:02d}.{msec:03d}"
