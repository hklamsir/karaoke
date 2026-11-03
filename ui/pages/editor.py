# -*- coding: utf-8 -*-
"""pages.editor — 字幕編輯器（計劃書 §6；版面依據 docs/線框圖/02_編輯器.html）。

C1 雙軌：上歌詞樹（Treeview）／下 Canvas 時間軸／右逐字微調精確 ms —— 三者雙向同步，
全部由 EditorState 單一數據源推導（R22）。
音訊（commit 6）／大 preview 窗（commit 7）／估算字功能（commit 8）／燒錄（commit 9）逐步填入。
"""

from __future__ import annotations

import logging
import subprocess
import threading
import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from pathlib import Path

import ttkbootstrap as ttkb

from .. import models, state, theme
from ..commands import DeleteLine, InsertWord, MergeLines, SetCharTiming, SetLineText, SplitLine
from ..widgets.lyric_tree import LyricTree
from ..widgets.progress import QueueLogHandler
from ..widgets.timeline import TimelineCanvas
from ..widgets.tooltip import Tooltip

FETCH_MS = 100      # ←→ 游標步長
FINE_MS = 10        # Shift ←→
FRAME_MS = 17       # Ctrl ←→ ≈ 1 帧 @60fps

PREVIEW_EXTRA_H = 80    # 用戶需求：影片預覽窗格 +80px（時間軸相應 -80px）→ 波形 192 擠得落
MIN_TIMELINE_H = 200    # 時間軸最少高度：再縮就見唔到波形 + 字幕塊
LEFT_W = 386            # 左欄闊度：歌詞樹 5 欄 370 + scrollbar 8 + 邊框/內距 8

LOGGER = logging.getLogger("karaoke.ui.editor")


class EditorPage(ttkb.Frame):
    """字幕編輯器。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="light")
        self._state = state.EditorState()
        self._audio = None          # commit 6
        self._audio_note = ""
        self._thumbs = None         # commit 7
        # §2.4：游標輪詢鏈只准有一條。以前每次 _on_audio_ready 都叫一次
        # _poll_position()，而該方法尾又自我重排 → 開 3 個檔就有 3 條
        # 永不停止的 30ms 鏈，每條都觸發全量重繪（CPU 白燒、越用越卡）。
        self._polling = False
        self._edit_target_i = None  # inline 修改歌詞目標行

        self._build_toolbar()
        body = ttkb.Frame(self, bootstyle="light")
        body.pack(fill="both", expand=True, pady=(theme.SPACE, 0))

        # --- 左欄：歌詞樹 + 逐字微調 ---
        # 用戶需求：歌詞樹 5 欄總寬 = 370px → 欄寬要 370，加埋垂直 scrollbar(8)、
        # LabelFrame 邊框(4)、內距(4) = LEFT_W 386，先可以一欄都唔裁。
        self._left = left = ttkb.Frame(body, bootstyle="light", width=LEFT_W)
        left.pack(side="left", fill="y", padx=(0, theme.SPACE))
        left.pack_propagate(False)

        tree_box = ttkb.LabelFrame(left, text="歌詞樹", bootstyle="light")
        tree_box.pack(fill="both", expand=True)
        self._tree = LyricTree(tree_box, self._state,
                               on_play_line=self._play_line,
                               on_add_line=self._show_add_line_bar)
        self._tree.pack(fill="both", expand=True, padx=2, pady=2)

        row = ttkb.Frame(left, bootstyle="light")
        row.pack(fill="x", pady=(2, 0))
        ttkb.Button(row, text="✏ 修改", bootstyle="secondary", takefocus=False,
                    command=self._edit_selected_line).pack(side="left", padx=(0, 2))
        ttkb.Button(row, text="✂ 斷句", bootstyle="secondary", takefocus=False,
                    command=self._split_at_word).pack(side="left", padx=2)
        ttkb.Button(row, text="🔗 合併", bootstyle="secondary", takefocus=False,
                    command=self._merge_selected).pack(side="left", padx=2)
        ttkb.Button(row, text="🗑 刪行", bootstyle="secondary", takefocus=False,
                    command=self._delete_line).pack(side="left", padx=2)

        # --- 可拖曳分隔條（用戶需求 #2）：調整左欄（歌詞樹/逐字微調）闊度 ---
        self._left_w = LEFT_W
        self._left_sep = tk.Canvas(body, width=5, bg=theme.BG_HOVER,
                                   highlightthickness=0, cursor="sb_h_double_arrow")
        self._left_sep._theme_token = "BG_HOVER"   # 換主題時 _restyle_tree 用
        self._left_sep.pack(side="left", fill="y")
        self._left_sep.bind("<Button-1>", self._on_leftsep_press)
        self._left_sep.bind("<B1-Motion>", self._on_leftsep_drag)

        # --- inline 修改歌詞列（用戶回報彈窗「確定」兩次都冇生效 → 改 inline，
        #     冇 grab/focus 陷阱；Enter 套用 / Esc 取消）---
        self._edit_frame = ttkb.LabelFrame(left, text="修改歌詞", bootstyle="light")
        erow = ttkb.Frame(self._edit_frame, bootstyle="light")
        erow.pack(fill="x", padx=theme.SPACE, pady=(theme.SPACE, 2))
        self._edit_line_label = ttkb.Label(erow, text="第 -- 行", width=8,
                                           bootstyle="secondary")
        self._edit_line_label.pack(side="left")
        self._edit_line_var = tk.StringVar()
        self._edit_entry = ttkb.Entry(self._edit_frame, textvariable=self._edit_line_var)
        self._edit_entry.pack(fill="x", padx=theme.SPACE)
        self._edit_entry.bind("<Return>", lambda _e: self._apply_line_edit())
        self._edit_entry.bind("<Escape>", lambda _e: self._hide_line_edit())
        ebtns = ttkb.Frame(self._edit_frame, bootstyle="light")
        ebtns.pack(fill="x", padx=theme.SPACE, pady=(2, theme.SPACE))
        self._apply_btn = ttkb.Button(ebtns, text="✓ 套用 (Enter)",
                                      bootstyle=theme.ACCENT, takefocus=False,
                                      command=self._apply_line_edit)
        self._apply_btn.pack(side="right", padx=2)
        ttkb.Button(ebtns, text="取消 (Esc)", bootstyle="secondary", takefocus=False,
                    command=self._hide_line_edit).pack(side="right")
        # 預設收埋；pack 時用 before=self._ft_box 插喺逐字微調前面

        # --- inline 插字 / 新增歌詞行列（用戶回報 _ask_text 彈窗冇生效 →
        #     同「修改歌詞」一樣改 inline，徹底鏟走 Toplevel 焦點陷阱）---
        self._ask_mode: str | None = None      # "char" | "line" | None
        self._ask_ref: tuple[int, int, int] | None = None
        self._ask_frame = ttkb.LabelFrame(left, text="插入", bootstyle="light")
        arow = ttkb.Frame(self._ask_frame, bootstyle="light")
        arow.pack(fill="x", padx=theme.SPACE, pady=(theme.SPACE, 2))
        self._ask_label = ttkb.Label(arow, text="", bootstyle="secondary")
        self._ask_label.pack(side="left")
        self._ask_var = tk.StringVar()
        self._ask_entry = ttkb.Entry(self._ask_frame, textvariable=self._ask_var)
        self._ask_entry.pack(fill="x", padx=theme.SPACE)
        self._ask_entry.bind("<Return>", lambda _e: self._apply_ask())
        self._ask_entry.bind("<Escape>", lambda _e: self._hide_ask())
        self._ask_ok_btn = ttkb.Button(self._ask_frame, text="✓ 確定 (Enter)",
                                       bootstyle=theme.ACCENT, takefocus=False,
                                       command=self._apply_ask)
        self._ask_ok_btn.pack(side="right", padx=(2, theme.SPACE),
                              pady=(2, theme.SPACE))
        ttkb.Button(self._ask_frame, text="取消 (Esc)", bootstyle="secondary",
                    takefocus=False, command=self._hide_ask) \
            .pack(side="right", pady=(2, theme.SPACE))
        # 預設收埋；pack 時用 before=self._ft_box 插喺逐字微調前面

        self._build_finetune(left)

        # --- 右欄：大 preview 窗 + 可拖曳分隔條 + 時間軸 ---
        self._right = ttkb.Frame(body, bootstyle="light")
        self._right.pack(side="left", fill="both", expand=True)

        self._preview_slot = ttkb.LabelFrame(self._right, text="影片預覽", bootstyle="light")
        from ..widgets.preview import PreviewPane
        self._preview = PreviewPane(self._preview_slot, self._state, on_seek=self._on_seek)
        self._preview.pack(fill="both", expand=True, padx=2, pady=2)

        # 可拖曳分隔條（§6.7.2：預覽 30%–75%；V17）
        self._separator = tk.Canvas(self._right, height=8, bg=theme.BG_HOVER,
                                    highlightthickness=0, cursor="sb_v_double_arrow")
        self._separator._theme_token = "BG_HOVER"   # 換主題時 _restyle_tree 用
        self._separator.bind("<Button-1>", self._on_separator_press)
        self._separator.bind("<B1-Motion>", self._on_separator_drag)

        # 用戶需求 #4：時間軸要讓 100px 出嚟 → 圖例由獨立一行搬上 LabelFrame 標題
        # （原本獨立一行食 ~20px 垂直空間；搬上標題後零成本，訊息一樣睇得到）
        self._tl_box = ttkb.LabelFrame(
            self._right,
            text="時間軸 · 波形　字塊＝逐字時間（拖曳校）｜橙虛線＝估算字｜藍＝波形",
            bootstyle="light")
        self._timeline = TimelineCanvas(self._tl_box, self._state, on_seek=self._on_seek)
        # 縮放掣移入時間軸區域（用戶需求 #2）：同一行放 🔍－/🔍＋ + 水平捲軸
        zoombar = ttkb.Frame(self._tl_box, bootstyle="light")
        zoombar.pack(side="bottom", fill="x")
        ttkb.Button(zoombar, text="🔍－", width=3, bootstyle="secondary", takefocus=False,
                    command=lambda: self._state.zoom(-1)).pack(side="left", padx=(2, 1))
        ttkb.Button(zoombar, text="🔍＋", width=3, bootstyle="secondary", takefocus=False,
                    command=lambda: self._state.zoom(+1)).pack(side="left", padx=(1, 2))
        # 用戶需求：水平平移 slider 拆走 —— 底部概覽條（可拖）＋ Shift+滾輪
        # 已經做到同一件事，兩套控制容易混淆。呢度只留一句提示。
        ttkb.Label(zoombar,
                   text="拖底部藍色概覽條，或用 Shift + 滾輪左右平移；滾輪縮放",
                   bootstyle="secondary", font=(theme.ui_font_family(), 8)) \
            .pack(side="left", padx=(8, 0))
        self._timeline.pack(fill="both", expand=True, padx=2, pady=2)
        # 開檔後 fit（用戶需求 #3）：timeline 真正 layout 好（<Configure>，w>100）先量得準
        self._timeline.bind("<Configure>", lambda _e: self._zoom_fit_once())

        # 預覽窗格高度（px）——用戶需求 #3：由「比例制」改「像素制」，
        # 舊比例 0.55 × 右欄高 + 100px（-1 = 未量度，等 <Configure> 有高度先算）
        self._preview_ratio = 0.55
        self._preview_h = -1
        self._right.bind("<Configure>", lambda _e: self._relayout_right())
        self._relayout_right()

        # --- 狀態列 ---
        self._build_statusbar()

        self._state.add_observer(self._on_state_change)

    # ------------------------------------------------------------------
    # 工具列
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttkb.Frame(self, height=theme.TOOLBAR_H, bootstyle="light")
        bar.pack(fill="x")
        self._save_btn = ttkb.Button(bar, text="💾 儲存", bootstyle="secondary",
                                     takefocus=False, command=self.save)
        self._save_btn.pack(side="left", padx=(0, 2))
        self._burn_btn = ttkb.Button(bar, text="🔥 燒錄", bootstyle=theme.ACCENT,
                                     takefocus=False, command=self._burn)
        self._burn_btn.pack(side="left", padx=2)
        # ↶/↷ glyph JhengHei UI 冇 → 專屬 style 用 Segoe UI Symbol
        # （ttk Button 唔收 font 選項，要 style.configure）
        self._symbol_styles()
        self._undo_btn = ttkb.Button(bar, text="↶", width=3, takefocus=False,
                                     command=self.undo, style="Symbol.TButton")
        self._undo_btn.pack(side="left", padx=2)
        self._redo_btn = ttkb.Button(bar, text="↷", width=3, takefocus=False,
                                     command=self.redo, style="Symbol.TButton")
        self._redo_btn.pack(side="left", padx=2)
        # 用戶需求：↶/↷ 係純圖示，唔講明就唔知係 undo/redo（亦唔知做過乜）
        Tooltip(self._undo_btn, self._undo_hint)
        Tooltip(self._redo_btn, self._redo_hint)
        Tooltip(self._save_btn, "儲存 timings.json（Ctrl+S）")
        Tooltip(self._burn_btn, "依主頁樣式燒錄成 mp4（可換成自備的純音樂 MV）")

        ttkb.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self._btn_home = ttkb.Button(bar, text="⏮", width=3, bootstyle="secondary",
                                     takefocus=False, command=lambda: self._jump_line(-1))
        self._btn_home.pack(side="left", padx=2)
        self._play_btn = ttkb.Button(bar, text="▶", width=3, bootstyle="secondary",
                                     takefocus=False, command=self._toggle_play)
        self._play_btn.pack(side="left", padx=2)
        ttkb.Button(bar, text="⏭", width=3, bootstyle="secondary", takefocus=False,
                    command=lambda: self._jump_line(+1)).pack(side="left", padx=2)
        self._replay_btn = ttkb.Button(bar, text="重播3秒", bootstyle="secondary",
                                       takefocus=False, command=self._replay_3s)
        self._replay_btn.pack(side="left", padx=2)

        ttkb.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self._est_on = tk.BooleanVar(value=False)
        self._est_chip = ttkb.Checkbutton(bar, text="只顯示估算字", variable=self._est_on,
                                          bootstyle="primary-round-toggle",
                                          command=self._toggle_est_filter)
        self._est_chip.pack(side="left", padx=2)
        self._overlay_on = tk.BooleanVar(value=True)
        self._overlay_chip = ttkb.Checkbutton(
            bar, text="字幕疊圖", variable=self._overlay_on,
            bootstyle="primary-round-toggle", command=self._toggle_overlay)
        self._overlay_chip.pack(side="left", padx=2)
        ttkb.Button(bar, text="🎨", width=3, bootstyle="secondary", takefocus=False,
                    command=self._edit_style).pack(side="left", padx=2)
        # （時間軸縮放掣已移入時間軸區域，用戶需求 #2）

        self._time_var = tk.StringVar(value="00:00.000 / 00:00.000")
        ttkb.Label(bar, textvariable=self._time_var, bootstyle="info",
                   font=(theme.mono_font_family(), 11)).pack(side="right", padx=theme.SPACE)

    def _symbol_styles(self) -> None:
        """↶/↷ 專屬 style：Segoe UI Symbol 字型 + 跟 secondary 按鈕配色。

        顏色照抄 ``secondary.TButton``（同旁邊嘅 💾/🔥 一致）；換主題後要重跑，
        所以抽成獨立方法（``restyle`` 會再叫一次）。
        """
        _s = ttkb.Style()

        def _lk(opt: str, fallback: str) -> str:
            try:
                return _s.lookup("secondary.TButton", opt) or fallback
            except Exception:      # noqa: BLE001
                return fallback

        sec_bg, sec_fg = _lk("background", theme.BG_HOVER), _lk("foreground", theme.FG_MAIN)
        sec_bd = _lk("bordercolor", theme.BORDER)
        _s.configure("Symbol.TButton", font=("Segoe UI Symbol", 12),
                     background=sec_bg, foreground=sec_fg,
                     bordercolor=sec_bd, lightcolor=sec_bg,
                     darkcolor=sec_bg, relief="flat", padding=(4, 2))
        # disabled 狀態唔可以用近底色（掣會「消失」）；同底色一樣灰 + 淡字
        _s.map("Symbol.TButton",
               background=[("active", theme.BG_HOVER), ("disabled", sec_bg)],
               foreground=[("disabled", theme.FG_DIMMER)])

    def restyle(self) -> None:
        """換主題後重設自訂 style 與 tag 色（ttk 控件本身會跟 style 自動變）。"""
        self._symbol_styles()
        self._ft_tree.tag_configure("estimated", foreground=theme.WARNING)
        for btn in (self._burn_btn, getattr(self, "_apply_btn", None),
                    getattr(self, "_ask_ok_btn", None)):
            if btn is None:
                continue
            try:
                btn.configure(bootstyle=theme.ACCENT)
            except Exception:      # noqa: BLE001
                pass


    def _build_statusbar(self) -> None:
        bar = ttkb.Frame(self, bootstyle="light")
        bar.pack(fill="x", pady=(2, 0))
        self._status_var = tk.StringVar(value="未開檔")
        ttkb.Label(bar, textvariable=self._status_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 9)).pack(side="left", padx=theme.SPACE)

    # ------------------------------------------------------------------
    # 逐字微調面板（§6.3）
    # ------------------------------------------------------------------

    def _build_finetune(self, parent) -> None:
        self._ft_box = box = ttkb.LabelFrame(parent, text="逐字微調", bootstyle="light")
        box.pack(fill="both", pady=(theme.SPACE, 0))

        mono = theme.mono_font_family()
        self._ft_tree = ttkb.Treeview(box, columns=("ch", "word", "begin", "end"),
                                      show="headings", height=6, bootstyle="light")
        # 用戶需求：**「詞」要靠左對齊** —— 之前只設 column anchor="w"，但標題
        # 仍然置中，結果「詞」字浮喺右邊同下面嘅詞語對唔上，讀起上嚟唔知邊欄
        # 打邊欄。而家 heading 同 cell 用**同一個 anchor**，四欄一致。
        # 亦固定 ch/begin 闊度、由 word/end 吸收剩餘闊度，欄位唔會因視窗闊度跳位。
        for key, label, width, anchor, stretch in (
                ("ch", "字", 36, "center", False),
                ("word", "詞", 140, "w", True),
                ("begin", "begin", 64, "e", False),
                ("end", "end", 64, "e", True)):
            self._ft_tree.heading(key, text=label, anchor=anchor)
            self._ft_tree.column(key, width=width, anchor=anchor, stretch=stretch)
        self._ft_tree.tag_configure("estimated", foreground=theme.WARNING)
        self._ft_tree.pack(fill="both", expand=True, padx=2, pady=2)
        self._ft_tree.bind("<<TreeviewSelect>>", self._on_ft_select)
        self._ft_syncing = False   # 防 selection_set ↔ <<TreeviewSelect>> 無限循環

        row = ttkb.Frame(box, bootstyle="light")
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
        # ⚠️ 套用掣原本喺呢行尾——左欄 260px 唔夠闊，被裁出畫面外（用戶實測
        # 「冇確定掣」）。搬落 row2 頭，同斷句/插入字並排。

        row2 = ttkb.Frame(box, bootstyle="light")
        row2.pack(fill="x", padx=2, pady=(0, 2))
        ttkb.Button(row2, text="✓ 確定", bootstyle="secondary", takefocus=False,
                    command=self._apply_ft).pack(side="left", padx=(0, 2))
        ttkb.Button(row2, text="✂ 斷句", bootstyle="secondary", takefocus=False,
                    command=self._split_at_word).pack(side="left", padx=2)
        ttkb.Button(row2, text="＋ 插字", bootstyle="secondary", takefocus=False,
                    command=self._insert_char).pack(side="left", padx=2)
        ttkb.Button(row2, text="↻ 重劃", bootstyle="secondary", takefocus=False,
                    command=self._respawn_selected_word).pack(side="left", padx=2)
    # ------------------------------------------------------------------
    # 開檔 / 存檔（§5.3 / E2）
    # ------------------------------------------------------------------

    def load_timings(self, path: str | Path) -> None:
        """主頁 / 快捷鍵入口：讀 timings.json + 驗證原片存在（R8）+ 背景載入音訊。"""
        try:
            self._state.load(path)
        except Exception as exc:   # noqa: BLE001
            messagebox.showerror("開檔失敗", f"{path}\n\n{exc}")
            return
        # 預設縮放：成條片啱啱好塞滿時間軸（0 至片尾，用戶需求 #3）。
        # ⚠️ 要等右欄 layout 完成先量到準確畫布闊度 → 用 _zoom_fit_pending
        # 喺 _relayout_right 落手；300ms fallback 防視窗已 layout 唔再觸發 Configure。
        self._zoom_fit_pending = True
        self.after(300, self._zoom_fit_once)
        video = self._state.video_path
        if video and not video.is_file():
            if messagebox.askyesno("原片已移動",
                                   f"原片搵唔到：\n{video}\n\n重新指定原片？（燒錄要用）"):
                new = filedialog.askopenfilename(
                    title="重新指定原片",
                    filetypes=[("影片檔", "*.mp4 *.mkv *.mov *.webm *.avi"), ("全部檔案", "*.*")])
                if new:
                    self._state.reassign_video(new)
        self._load_audio()
        self._load_thumbs()
        # ADR-015：開檔即把該片樣式反映去主頁面板（所見即所燒）
        self._sync_home_style()

    def _sync_home_style(self) -> None:
        """``doc.style`` → 主頁樣式面板（燒錄以主頁為權威，兩者必須一致）。"""
        style = self._state.doc.get("style") or {}
        if not style:
            return
        getter = getattr(self.master, "page", None)
        home = getter("home") if callable(getter) else None
        if home is not None and hasattr(home, "apply_style"):
            try:
                home.apply_style(style)
            except Exception:   # noqa: BLE001 — 同步失敗唔應該阻住開檔
                LOGGER.exception("同步主頁樣式失敗")

    def on_home_style_changed(self, style: dict) -> None:
        """主頁改樣式 → 更新現有 doc.style 並重繪疊圖（ADR-015 單一來源）。

        Args:
            style: 主頁 ``style_snapshot()`` 結果（唔含 play_res）。
        """
        if not style or self._state.path is None:
            return
        current = self._state.doc.setdefault("style", {})
        changed = {k: v for k, v in style.items() if current.get(k) != v}
        if not changed:
            return
        current.update(changed)
        self._state.dirty = True
        self._state.notify("style")

    def _load_thumbs(self) -> None:
        """縮圖抽取（§6.7.4）：背景 thread + 快取；冇原片就顯示提示。"""
        from .. import settings as ui_settings
        video = self._state.video_path
        data = self.master.settings_data
        ffmpeg = ui_settings.detect_ffmpeg(data)
        ffprobe = ui_settings.detect_ffprobe(data)
        if video is None or not video.is_file() or not ffmpeg or not ffprobe:
            self._preview.start_thumbs(None, "", "")
            return
        self._preview.start_thumbs(video, ffmpeg, ffprobe)

    def _load_audio(self) -> None:
        """背景解碼音訊 + 波形（§6.6）；失敗靜音降級，其他功能照用。"""
        video = self._state.video_path
        if video is None or not video.is_file():
            self._audio_status("冇原片，靜音模式")
            return
        from .. import settings as ui_settings
        from ..audio import AudioEngine
        if self._audio is None:
            self._audio = AudioEngine()
        if not self._audio.available:
            self._audio_status("未裝 pygame，無法播放音訊（pip install pygame）")
            return
        ffmpeg = ui_settings.detect_ffmpeg(self.master.settings_data)
        if not ffmpeg:
            self._audio_status("搵唔到 ffmpeg，無法解碼音訊")
            return
        self._audio_status("抽取音訊中…")

        def on_done(engine) -> None:
            if engine is None or engine.peaks is None:
                self.after(0, lambda: self._audio_status("音訊解碼失敗（其他功能照用）"))
                return
            self.after(0, self._on_audio_ready)

        engine = self._audio
        engine.load_video_async(
            ffmpeg, video, on_done,
            on_progress=lambda msg: self.after(0, lambda m=msg: self._audio_status(m)))

    def _on_audio_ready(self) -> None:
        engine = self._audio
        if engine is None or engine.peaks is None:
            return
        self._timeline.set_waveform(engine.peaks, engine.duration_ms)
        status = f"● 音訊 {engine.duration_ms / 1000:.0f}s 已載入"
        if getattr(engine, "playback_degraded", False):
            status += "（長片：只顯示波形，播放已停用）"
        self._audio_status(status)
        self._ensure_polling()

    def _audio_status(self, text: str) -> None:
        self._audio_note = text
        self._refresh_status()

    def _status_tail(self) -> str:
        st = self._state
        if st.path is None:
            return "未開檔"
        s = st.stats()
        est_pct = f"{s['estimated_ratio'] * 100:.0f}%" if s["estimated_total_words"] else "—"
        dirty = "●未儲存" if st.dirty else "已儲存"
        total = models.total_duration_ms(st.doc)
        return (f"游標 {_fmt_ms(st.cursor_ms)}/{_fmt_ms(total)} │ "
                f"{s['num_lines']}行·{s['num_chars']}字 │ ●估算 {s['estimated_words']}詞 {est_pct} │ "
                f"{dirty} │ {st.path.name}")

    def _ensure_polling(self) -> None:
        """啟動游標輪詢鏈 —— 但只准一條（§2.4 P0）。

        以前 ``_on_audio_ready`` 直接叫 ``_poll_position()``，而該方法尾部
        無條件 ``self.after(30, self._poll_position)``：每換一次音源就多一條
        永不停止的定時鏈。三條鏈同時跑 = 每 30ms 三次全量重繪（timeline +
        preview + 字幕卡），而且每條都獨立計算，關掉檔案亦唔會停。

        用旗標保證冪等；鏈條自己結束（engine 不在）時會清旗標，令下次載入
        音源仍然可以重新啟動。
        """
        if self._polling:
            return
        self._polling = True
        self._poll_position()

    def _poll_position(self) -> None:
        """播放中每 30ms 更新游標（§6.6）。"""
        engine = self._audio
        if engine is None or not engine.available:
            # 鏈條到此結束：唔重排，但**必須**清旗標，否則之後永遠唔會再啟動
            self._polling = False
            return
        if engine.playing:
            self._state.set_cursor(engine.position_ms())
            if self._play_btn["text"] != "⏸":
                self._play_btn.configure(text="⏸")
        else:
            if self._play_btn["text"] != "▶":
                self._play_btn.configure(text="▶")
        self.after(30, self._poll_position)

    def _toggle_play(self) -> None:
        engine = self._audio
        if engine is None or not engine.available:
            messagebox.showinfo("靜音模式", "音訊未載入（未裝 pygame 或冇原片）。")
            return
        if getattr(engine, "playback_degraded", False):
            # §2.6：長片只載 peaks（省記憶體），播放功能主動停用並講明原因，
            # 唔可以「按了冇反應」—— 用戶會以為程式壞。
            messagebox.showinfo(
                "長片模式",
                "音訊超過 30 分鐘，為節省記憶體只載入波形，播放已停用。\n"
                "可用工具列的「外部播放器」跟住唱。", parent=self)
            return
        if engine.playing:
            # 暫停：記住而家位置做下次起點
            self._state.set_cursor(engine.position_ms())
        engine.toggle(self._state.cursor_ms)

    def _replay_3s(self) -> None:
        """「重播 3 秒」跟唱：播 [游標−1s, 游標+2s]（§6.6）。"""
        engine = self._audio
        if engine is None or not engine.available:
            return
        engine.play_range(self._state.cursor_ms - 1000, self._state.cursor_ms + 2000)

    # ------------------------------------------------------------------
    # 燒錄（F1：入 UI，喺編輯器工具列；背景 thread + 分階段進度）
    # ------------------------------------------------------------------

    def _burn(self) -> None:
        st = self._state
        if st.path is None:
            messagebox.showinfo("燒錄", "先開一份 *_timings.json。")
            return
        video = st.video_path
        if video is None or not video.is_file():
            messagebox.showerror("燒錄", "原片唔存在（先喺開檔時「重新指定原片」）。")
            return
        from .. import settings as ui_settings
        ffmpeg = ui_settings.detect_ffmpeg(self.master.settings_data)
        if not ffmpeg:
            messagebox.showerror("燒錄", "搵唔到 ffmpeg（設定頁填路徑）。")
            return
        ffprobe = ui_settings.detect_ffprobe(self.master.settings_data)

        # ADR-015：主頁樣式 = 燒錄權威來源（用戶回報「燒錄冇跟主頁設定」）
        home_style = self._home_style()

        # ADR-020：燒錄音源可換成用戶自備的純音樂 MV（AI 去人聲已否決）
        from ..widgets.burn_dialog import ask_burn_source

        last_dir = Path(self.master.settings_data.get("last_burn_dir") or video.parent)
        source = ask_burn_source(
            self,
            default_video=video,
            style_text=_describe_style(home_style),
            ffprobe=ffprobe,
            last_dir=last_dir,
        )
        if source is None:
            return
        # 記住揀檔資料夾（同「已儲存的值」比較，唔係同預設值比較 ——
        # 否則第一次揀同一個資料夾就永遠唔會寫入）
        stored_dir = str(self.master.settings_data.get("last_burn_dir") or "")
        if str(source.parent) != stored_dir:
            data = dict(self.master.settings_data)
            data["last_burn_dir"] = str(source.parent)
            self.master.save_settings(data)

        # 1) 把主頁樣式寫入 doc（令 timings.json 同燒錄結果一致）→ 儲存
        if home_style:
            st.doc.setdefault("style", {}).update(home_style)
            st.dirty = True
        try:
            st.save()
        except Exception as exc:   # noqa: BLE001
            messagebox.showerror("燒錄", f"儲存失敗：{exc}")
            return

        # 2) render 最新 ass（唔可以直接寫 ass 畀人手改 —— 係 burn 嘅中間產物）
        doc = st.doc
        ass_path = st.path.with_name(st.path.stem.replace("_timings", "_karaoke") + ".ass")
        # 輸出跟「實際燒錄的那條片」命名；換了純音樂 MV 就唔會覆蓋原片的成品
        output = source.with_name(f"{source.stem}_karaoke.mp4")
        strip_punct = bool((doc.get("source") or {}).get("strip_punctuation", True))
        import main as _main
        _, doc_style = _main.timings_doc_to_lines(doc)
        if home_style:
            # 顯式覆寫：連舊檔 style 缺欄位都保證跟主頁
            from dataclasses import replace as _replace
            doc_style = _replace(doc_style, **home_style)
        ass_text = _main.render_timings(doc, doc_style, strip_punctuation=strip_punct)
        # 原子寫入（§2.8）：燒錄中途斷電唔應該留下一份半截 .ass 覆蓋舊的
        _main.atomic_write_text(ass_path, ass_text)
        LOGGER.info("燒錄樣式：%s", _describe_style(home_style))
        LOGGER.info("燒錄音源：%s（字幕時間軸基準：%s）", source.name, video.name)

        # 3) 進度窗（置中 + log 預設展開；用戶回報 dialog 冇置中、睇唔到 log）
        from ..widgets.dialogs import center_window
        from ..widgets.progress import StageProgress
        win_size = (680, 460)
        win = ttkb.Toplevel(title="燒錄中…", size=win_size)
        win.transient(self.winfo_toplevel())
        win.minsize(560, 360)
        btn_row = ttkb.Frame(win, bootstyle="light")
        btn_row.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        # 取消燒錄（ADR-025）：燒錄長片可達數十分鐘，以前唯一選擇係等或殺程式
        cancel_event = threading.Event()
        cancel_btn = ttkb.Button(btn_row, text="✖ 取消燒錄", bootstyle="danger-outline",
                                 takefocus=False, command=lambda: request_cancel())
        cancel_btn.pack(side="right", padx=(0, 6))
        ttkb.Button(btn_row, text="關閉", bootstyle="secondary", takefocus=False,
                    command=win.destroy).pack(side="right")
        prog = StageProgress(win, stages=["燒錄"], log_open=True, log_height=14)
        prog.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        center_window(win, self, win_size)
        self._burn_btn.configure(state="disabled")
        handler = QueueLogHandler(prog)
        logging.getLogger("karaoke").addHandler(handler)

        def request_cancel() -> None:
            """送出取消訊號。真正終止 ffmpeg 由 main._run_ffmpeg 的 poll 迴圈做。"""
            cancel_event.set()
            cancel_btn.configure(state="disabled", text="取消中…")
            prog.log("已送出取消訊號，正在終止 ffmpeg …")

        def on_error(msg: str) -> None:
            prog.fail(msg)
            self._safe_title(win, "燒錄失敗 ✕")
            messagebox.showerror("燒錄失敗", msg, parent=self)

        def worker() -> None:
            try:
                _main.burn_video(source, ass_path, output, progress_cb=prog.report,
                                 cancel_event=cancel_event)
            except _main.TaskCancelled:
                # 取消唔算失敗：唔彈 error dialog（半截輸出檔已由 burn_subtitles 刪除）
                prog.fail("已取消（未產生輸出檔）")
                self.after(0, lambda: self._safe_title(win, "燒錄已取消"))
            except SystemExit as exc:
                # 保險層：ADR-024 起 burn_video 拋 BurnError（落到下面 Exception 分支，
                # 顯示「ffmpeg 燒錄失敗（rc=…）」而不是光禿禿一個 exit code）。
                # ⚠️ Python 3 喺 except block 結束時刪除 exc，lambda 延遲執行會 NameError
                code = exc.code
                self.after(0, lambda code=code: on_error(
                    f"ffmpeg 失敗（exit {code}），詳見 log"))
            except Exception as exc:   # noqa: BLE001
                err = str(exc)
                self.after(0, lambda err=err: on_error(err))
            else:
                prog.succeed("完成 ✅")
                # 唔自動關窗 —— log 預設打開，畀用戶自己睇完再關
                self.after(0, lambda: self._safe_title(win, "燒錄完成 ✅"))
                self.after(0, lambda: self._on_burn_done(output))
            finally:
                logging.getLogger("karaoke").removeHandler(handler)
                self.after(0, lambda: self._burn_btn.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _safe_title(win, text: str) -> None:
        """視窗可能已被用戶關咗 → 改標題要吞 TclError。"""
        try:
            if win.winfo_exists():
                win.title(text)
        except tk.TclError:
            pass

    def _home_style(self) -> dict:
        """主頁樣式面板當前值；冇主頁（測試 / 單獨開 editor）就回空 dict。"""
        getter = getattr(self.master, "page", None)
        home = getter("home") if callable(getter) else None
        if home is None or not hasattr(home, "style_snapshot"):
            return {}
        try:
            return home.style_snapshot()
        except Exception:   # noqa: BLE001 — 樣式同步失敗唔應該阻住燒錄
            LOGGER.exception("讀取主頁樣式失敗，改用檔案內嵌樣式")
            return {}

    def _on_burn_done(self, output: Path) -> None:
        if messagebox.askyesno("燒錄完成 ✅", f"{output}\n\n開啟資料夾？", parent=self):
            subprocess.Popen(["explorer", "/select,", str(output)])

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

    #: command 類名 → 人話（提示用）
    _CMD_NAMES = {
        "SetCharTiming": "調整字時間",
        "SetWordShift": "平移字塊",
        "SetLineText": "修改歌詞",
        "SplitLine": "斷句",
        "MergeLines": "合併句子",
        "InsertWord": "插入字詞",
        "RespreadWord": "重新分配時間",
        "AddLine": "新增歌詞行",
        "DeleteLine": "刪除歌詞行",
    }

    def _cmd_name(self, cmd) -> str:
        return self._CMD_NAMES.get(type(cmd).__name__, type(cmd).__name__)

    def _undo_hint(self) -> str:
        """復原掣嘅提示：連「會回退邊一步」都講埋（純圖示要自解）。"""
        cmd = self._state.undo_stack.peek_undo()
        if cmd is None:
            return "復原（Ctrl+Z）\n目前沒有可復原的操作"
        return f"復原：{self._cmd_name(cmd)}（Ctrl+Z）\n快捷鍵：Ctrl+Z"

    def _redo_hint(self) -> str:
        cmd = self._state.undo_stack.peek_redo()
        if cmd is None:
            return "重做（Ctrl+Shift+Z / Ctrl+Y）\n目前沒有可重做的操作"
        return f"重做：{self._cmd_name(cmd)}（Ctrl+Shift+Z）\n快捷鍵：Ctrl+Shift+Z 或 Ctrl+Y"

    def undo(self) -> None:
        if not self._state.undo():
            self.winfo_toplevel().bell()

    def redo(self) -> None:
        if not self._state.redo():
            self.winfo_toplevel().bell()

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

    def _play_line(self, line_i: int) -> None:
        """雙擊歌詞樹句子：跳到該句開始時間並播放（預覽 + 時間軸同步）。"""
        lines = self._state.lines
        if not (0 <= line_i < len(lines)):
            return
        self._state.set_current_line(line_i)
        ms = int(lines[line_i].get("start_ms", 0))
        self._state.set_cursor(ms)
        self._on_seek(ms)
        engine = self._audio
        if engine is not None and engine.available and not engine.playing:
            engine.play(ms)

    def _edit_selected_line(self) -> None:
        """✏ 修改（inline 版）：點選句子 → 撳掣 → 歌詞樹下面出現編輯列。

        彈窗版（_ask_text）兩輪修 grab/focus 之後用戶實機仍然回報冇生效，
        改 inline 徹底鏟走 Toplevel 焦點問題。
        """
        sel = self._tree.selected_lines()
        if not sel:
            messagebox.showinfo("修改歌詞", "先喺歌詞樹點選一行。")
            return
        line_i = sel[0]
        self._edit_target_i = line_i
        self._edit_line_label.configure(text=f"第 {line_i + 1} 行")
        self._edit_line_var.set(self._state.lines[line_i].get("text") or "")
        self._edit_frame.pack(fill="x", pady=(2, 0), before=self._ft_box)
        self._edit_entry.focus_force()
        self._edit_entry.select_range(0, "end")

    def _apply_line_edit(self) -> None:
        if self._edit_target_i is None:
            return
        new = self._edit_line_var.get().strip()
        old = self._state.lines[self._edit_target_i].get("text") or ""
        if new and new != old:
            self._state.push(SetLineText(self._edit_target_i, new))
        self._hide_line_edit()

    def _hide_line_edit(self) -> None:
        self._edit_target_i = None
        self._edit_frame.pack_forget()

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
        """＋ 插字（inline 版）：新字插喺選中字前面，原字時間軸不變。

        用戶回報 _ask_text 彈窗冇生效 → 改 inline 列（同「修改歌詞」同款）。
        """
        ref = self._state.selected
        if ref is None:
            messagebox.showinfo("插入字", "先喺時間軸 / 逐字微調選中一個字（新字會插喺佢前面）。")
            return
        line_i, word_i, char_i = ref
        # 插入後舊 ref 可能過期（words/chars 已變）→ 過期就要求重新選（用戶實測 IndexError）
        try:
            char = self._state.lines[line_i]["words"][word_i]["chars"][char_i]
        except IndexError:
            self._state.set_selection(None)
            messagebox.showinfo("插入字", "之前選嘅字已經變咗（可能啱啱插過字），請重新選一次。")
            return
        self._show_ask_bar(
            "char", ref,
            f"新字會插喺「{char.get('text', '')}」前面（原字時間軸不變）：")

    def _show_add_line_bar(self) -> None:
        """＋ 新增歌詞行（inline 版）：由歌詞樹掣觸發，時間 = 尾行後 +3 秒。"""
        self._show_ask_bar("line", None, "新行歌詞（時間：尾行後 +3 秒，之後喺時間軸校）：")

    def _show_ask_bar(self, mode: str, ref, prompt: str) -> None:
        self._ask_mode = mode
        self._ask_ref = ref
        self._ask_label.configure(text=prompt, bootstyle="secondary")
        self._ask_var.set("")
        self._ask_frame.pack(fill="x", pady=(2, 0), before=self._ft_box)
        self._ask_entry.focus_force()

    def _hide_ask(self) -> None:
        self._ask_mode = None
        self._ask_ref = None
        self._ask_frame.pack_forget()

    def _apply_ask(self) -> None:
        text = self._ask_var.get().strip()
        if self._ask_mode == "char":
            # 之前 len != 1 會靜默唔做（用戶以為冇生效）→ 而家 inline 提示
            if len(text) != 1:
                self._ask_label.configure(
                    text="請輸入**一個**字（淨係收一個字；要加多字請逐個插）",
                    bootstyle="danger")
                return
            line_i, word_i, _char_i = self._ask_ref
            self._state.push(InsertWord(line_i, word_i, text))
        elif self._ask_mode == "line":
            if not text:
                self._ask_label.configure(text="請輸入歌詞", bootstyle="danger")
                return
            from ..commands import AddLine
            last_end = 0
            if self._state.lines:
                last_end = int(self._state.lines[-1].get("end_ms", 0))
            self._state.push(AddLine(last_end + 500, last_end + 3500, text))
        else:
            return
        self._hide_ask()

    # ------------------------------------------------------------------
    # 逐字微調
    # ------------------------------------------------------------------

    def _on_state_change(self, reason: str) -> None:
        if reason in ("load", "edit", "save", "select", "follow"):
            self._refresh_finetune()
        self._refresh_buttons()
        self._refresh_status()
        self._update_time_display()
    def _refresh_finetune(self) -> None:
        st = self._state
        # ⚠️ selection_set 會同步觸發 <<TreeviewSelect>> → _on_ft_select → set_selection
        #    → notify → 呢度 —— 無限循環（實測 freeze）。程式化同步要 flag 擋住。
        self._ft_syncing = True
        try:
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
        finally:
            self._ft_syncing = False
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
        if self._ft_syncing:
            return   # 程式化 selection_set 觸發嘅事件，唔好迴響
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

    def _toggle_overlay(self) -> None:
        self._preview.set_overlay_visible(self._overlay_on.get())

    def _toggle_est_filter(self) -> None:
        self._state.toggle_filter_estimated()

    # ------------------------------------------------------------------
    # 樣式色彩（用戶需求 #3）
    # ------------------------------------------------------------------

    def _edit_style(self) -> None:
        """🎨 樣式：直接改**現有 doc** 嘅唱過/未唱/描邊色（疊圖即時生效，
        儲存後持久）。主頁揀色只影響下次生成 —— 載入舊檔要喺呢度改。"""
        import tkinter.colorchooser as colorchooser
        style = self._state.doc.setdefault("style", {})
        defaults = {"primary_color": theme.SUB_PRIMARY,
                    "secondary_color": theme.SUB_SECONDARY,
                    "outline_color": "#000000"}
        labels = {"primary_color": "唱過色", "secondary_color": "未唱色",
                  "outline_color": "描邊色"}
        dlg = ttkb.Toplevel(title="樣式顏色", size=(330, 190))
        dlg.transient(self.winfo_toplevel())
        vars_: dict[str, tk.StringVar] = {}
        swatches: dict[str, tk.Canvas] = {}
        for key, label in labels.items():
            row = ttkb.Frame(dlg, bootstyle="light")
            row.pack(fill="x", padx=12, pady=4)
            ttkb.Label(row, text=label, width=8, bootstyle="secondary").pack(side="left")
            cur = style.get(key) or defaults[key]

            def pick(k=key, v=vars_) -> None:
                rgb = colorchooser.askcolor(color=swatches[k]["bg"], parent=dlg)
                if rgb and rgb[1]:
                    vars_[k].set("#{:02X}{:02X}{:02X}".format(*[round(c) for c in rgb[0]]))

            sw = tk.Canvas(row, width=26, height=20, bg=cur, highlightthickness=1,
                           highlightbackground=theme.BORDER, cursor="hand2")
            sw.pack(side="left")
            sw.bind("<Button-1>", lambda _e, p=pick: p())
            swatches[key] = sw
            var = tk.StringVar(value=cur)
            var.trace_add("write", lambda *_a, k=key: self._safe_swatch(swatches, k, vars_))
            ttkb.Entry(row, textvariable=var, width=10).pack(side="left", padx=(4, 0))
            vars_[key] = var

        def apply() -> None:
            changed = False
            for key, var in vars_.items():
                v = var.get().strip()
                if len(v) == 6:
                    v = "#" + v
                if len(v) == 7:
                    try:
                        int(v[1:], 16)
                    except ValueError:
                        continue
                    if style.get(key) != v:
                        style[key] = v
                        changed = True
            if changed:
                self._state.dirty = True
                self._state.notify("edit")
                self._sync_home_style()   # 主頁係燒錄權威 → 改色要同步過去
            dlg.destroy()

        btns = ttkb.Frame(dlg, bootstyle="light")
        btns.pack(fill="x", padx=12, pady=10)
        ttkb.Button(btns, text="確定", bootstyle=theme.ACCENT, command=apply) \
            .pack(side="right", padx=4)
        ttkb.Button(btns, text="取消", bootstyle="secondary",
                    command=dlg.destroy).pack(side="right")
        from ..widgets.dialogs import center_window
        center_window(dlg, self, (330, 190))
        try:
            dlg.wait_visibility()
            dlg.grab_set()
        except tk.TclError:
            pass

    @staticmethod
    def _safe_swatch(swatches: dict, key: str, vars_: dict) -> None:
        value = vars_[key].get().strip()
        if len(value) == 6:
            value = "#" + value
        if len(value) == 7:
            try:
                int(value[1:], 16)
                swatches[key]["bg"] = value
            except ValueError:
                pass

    def _respawn_selected_word(self) -> None:
        """↻ 重新劃分（C4）：選中 word 內部 char 按字數比例重分 → 清 estimated。"""
        ref = self._state.selected
        if ref is None:
            messagebox.showinfo("重新劃分", "先選中一個字（劃分佢所屬嘅詞）。")
            return
        from ..commands import RespreadWord
        self._state.push(RespreadWord(ref[0], ref[1]))

    def _relayout_right(self) -> None:
        """分隔條版面（§6.1）：預覽窗格用**像素高度**，其餘畀時間軸。

        用戶需求 #3／#4：預覽 +100px、時間軸相應 -100px。像素制（而唔係比例制）
        先可以保證「+100px」係實數 —— 比例制喺任何視窗高度下都會縮水。
        """
        h = self._right.winfo_height()
        if h < 120:
            return
        if self._preview_h <= 0:
            # 首次量度：舊比例 0.55 × 右欄高，再加 100px
            self._preview_h = self._clamp_preview_h(int(h * self._preview_ratio)
                                                   + PREVIEW_EXTRA_H, h)
        prev_h = self._clamp_preview_h(self._preview_h, h)
        self._preview_slot.place(x=0, y=0, relwidth=1.0, height=prev_h)
        sep_y = prev_h
        self._separator.place(x=0, y=sep_y, relwidth=1.0, height=8)
        tl_y = sep_y + 8
        self._tl_box.place(x=0, y=tl_y, relwidth=1.0, relheight=1.0, height=-tl_y)
        self._zoom_fit_once()

    @staticmethod
    def _clamp_preview_h(value: int, right_h: int) -> int:
        """夾住預覽高度：最少 120px、最多留 160px 畀時間軸（原本 30%–75% 嘅等價保護）。"""
        lo = 120
        hi = max(lo, right_h - MIN_TIMELINE_H)
        return int(max(lo, min(hi, value)))

    def _zoom_fit_once(self) -> None:
        """開檔後第一次 layout 就 fit（用戶需求 #3：時間軸預設 0 至片尾）。"""
        if getattr(self, "_zoom_fit_pending", False) and self._state.lines:
            w = self._timeline.winfo_width()
            if w > 100:
                self._zoom_fit_pending = False
                self._state.zoom_fit(w)

    def _on_separator_press(self, event) -> None:
        self._sep_y0 = event.y_root
        self._sep_h0 = self._clamp_preview_h(self._preview_h,
                                            self._right.winfo_height())

    def _on_separator_drag(self, event) -> None:
        h = max(self._right.winfo_height(), 1)
        dy = event.y_root - getattr(self, "_sep_y0", event.y_root)
        base = getattr(self, "_sep_h0", self._preview_h)
        new_h = self._clamp_preview_h(base + dy, h)
        if new_h != self._preview_h:
            self._preview_h = new_h
            self._relayout_right()

    # ------------------------------------------------------------------
    # 左欄闊度拖曳（用戶需求 #2：歌詞樹 / 逐字微調欄可調寬）
    # ------------------------------------------------------------------

    LEFT_MIN_W = 200   # 歌詞樹 4 欄最少要嘅闊度
    LEFT_MAX_W = 560   # 唔好食埋右欄（預覽 + 時間軸）

    def _on_leftsep_press(self, event) -> None:
        pass   # B1-Motion 直接用指標位置計

    def _on_leftsep_drag(self, event) -> None:
        pointer_x = self._left_sep.winfo_x() + event.x     # body 內指標位置
        new_w = max(self.LEFT_MIN_W, min(self.LEFT_MAX_W,
                                         pointer_x - theme.SPACE))
        if new_w != self._left_w:
            self._left_w = new_w
            self._left.configure(width=new_w)

    def _refresh_buttons(self) -> None:
        self._undo_btn.configure(
            state="normal" if self._state.undo_stack.can_undo() else "disabled")
        self._redo_btn.configure(
            state="normal" if self._state.undo_stack.can_redo() else "disabled")

    def _refresh_status(self) -> None:
        st = self._state
        if st.path is None:
            self._status_var.set(self._audio_note or "未開檔")
            return
        note = f"{self._audio_note}　│　" if self._audio_note else ""
        self._status_var.set(note + self._status_tail())

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


def _describe_style(style: dict) -> str:
    """把樣式 dict 變成人讀嘅一句（燒錄確認 / log 用）。"""
    if not style:
        return "檔案內嵌樣式（主頁未提供）"
    align_names = {2: "底部置中", 3: "中下", 5: "中部置中", 8: "頂部置中"}
    margin = int(style.get("margin_v") or 0)
    outline = float(style.get("outline_width") or 0.0)
    return (
        f"{style.get('font_name', '?')} {style.get('font_size', '?')}px · "
        f"唱過 {style.get('primary_color', '?')} / 未唱 {style.get('secondary_color', '?')} / "
        f"描邊 {style.get('outline_color', '?')}"
        f"（{'自動' if not outline else f'{outline:g}px'}）· "
        f"{align_names.get(int(style.get('alignment') or 2), '底部置中')} · "
        f"留白 {'自動' if not margin else f'{margin}px'}"
    )
