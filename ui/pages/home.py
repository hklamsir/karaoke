# -*- coding: utf-8 -*-
"""pages.home — 主頁（計劃書 §5.1–§5.3；版面依據 docs/線框圖/01_主頁.html）。

三層屬性（B2 決定）：基本（影片/語言/輸出）/ 樣式（字體顏色留白）/ 進階（預設收埋）。
右欄：即時樣式預覽（B3）+ 產出清單 + 「開始生成」主 CTA + 分階段進度（B4）。
"""

from __future__ import annotations

import logging
import re
import threading
import tkinter as tk
import tkinter.colorchooser as colorchooser
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
import subprocess
import json
from pathlib import Path
from typing import Any

import ttkbootstrap as ttkb

from .. import settings, theme
from ..widgets.progress import QueueLogHandler, StageProgress
from ..widgets.style_preview import StylePreviewCanvas

LOGGER = logging.getLogger("karaoke.ui.home")

VIDEO_PATTERNS = [("影片檔", "*.mp4 *.mkv *.mov *.webm *.avi"), ("全部檔案", "*.*")]

_ALIGNMENTS = [("底部置中", 2), ("中下", 3), ("頂部置中", 8), ("中部置中", 5)]
_OUTLINE_WIDTHS = [("自動", 0.0), ("幼 2", 2.0), ("中 3", 3.0), ("粗 4", 4.0)]

#: 反查表：ASS alignment 數值 → 中文名（開啟舊檔同步樣式時用）
_ALIGN_NAME = {code: name for name, code in _ALIGNMENTS}

#: ADR-030：雙行水平錯位（% 畫面闊度）；對應 ASS 的 \pos x 偏移
_POSITIONS = [("偏左", -10), ("居中", 0), ("偏右", 10)]
_POS_NAME = {value: name for name, value in _POSITIONS}

#: 常見中文字型關鍵字（排序用）：命中就排前，方便一次過揀到明體／黑體／楷體
_CJK_FONT_HINTS = (
    "mingliu", "pmingliu", "jhenghei", "yahei", "simsun", "simhei", "kaiti",
    "dfkai", "fangsong", "noto sans cjk", "noto serif cjk", "source han",
    "heiti", "songti", "kaiti", "ms gothic", "ms mincho", "yu gothic",
    "yu mincho", "meiryo", "pingfang", "hiragino", "wenquanyi", "droid sans fallback",
    "lihei", "uming", "ukai",
)
#: 反查表：描邊 px → 中文名
_OUTLINE_NAME = {width: name for name, width in _OUTLINE_WIDTHS}

_DEFAULT_FONT = "Microsoft JhengHei"

#: 找不到儲存字型時的優先序（全部要係「實際存在」的字型名；Windows 10/11 常見組合）
_FONT_PREFERENCE = (
    "Microsoft JhengHei", "Microsoft JhengHei UI", "Microsoft YaHei",
    "PingFang TC", "Noto Sans CJK TC", "Noto Sans CJK HK", "Source Han Sans TC",
    "PMingLiU", "MingLiU", "SimSun", "Noto Serif CJK TC",
)

#: `#RRGGBB` / `RRGGBB` —— 色塊只認呢個格式（Tk 對 `#0000F` 之類會直接拋 TclError）
_HEX_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


def _normalise_hex(value: str) -> str | None:
    """把用戶輸入正規化成 `#RRGGBB`（大寫）；唔合法就返 ``None``。

    Args:
        value: 用戶輸入（可帶／唔帶 `#`，大小寫不限）。

    Returns:
        正規化顏色字串；格式唔啱就 ``None``（呼叫者負責保留舊色）。
    """
    text = (value or "").strip()
    if not _HEX_RE.match(text):
        return None
    if not text.startswith("#"):
        text = "#" + text
    return text.upper()


class HomePage(ttkb.Frame):
    """主頁：新任務 / 開啟現有 / 三層屬性面板 / 樣式預覽 / 分階段進度。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="light")
        self._video_path: Path | None = None
        self._video_info: dict[str, Any] = {}
        # 任務互斥（ADR-025）：取代原本的 `_running` 布林旗標。
        # 布林旗標係 check-then-act —— 兩條 thread 同時讀到 False 就會一齊過關；
        # SingleFlight 用 Lock.acquire(blocking=False)，係原子操作。
        # 延遲建立：main 係選配相依（純 UI 預覽唔一定 import 到）。
        self._flight: Any | None = None
        self._cancel_event: Any | None = None
        # 上次主頁樣式（ADR-015：主頁 = 燒錄權威來源，要跨 session 記住）
        self._saved_style: dict[str, Any] = dict(
            master.settings_data.get("home_style") or {})
        self._style_save_job: str | None = None

        left = ttkb.Frame(self, bootstyle="light")
        left.pack(side="left", fill="both", expand=True, padx=(0, theme.SPACE))
        right = ttkb.Frame(self, bootstyle="light")
        right.pack(side="left", fill="y", padx=(0, theme.SPACE))

        self._build_basic(left)
        self._build_style(left)
        self._build_advanced(left)
        self._build_right(right)

    # ------------------------------------------------------------------
    # 左欄 · 基本
    # ------------------------------------------------------------------

    def _build_basic(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="基本", bootstyle="light")
        box.pack(fill="x", pady=(0, theme.SPACE))

        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(theme.SPACE, 2))
        ttkb.Label(row, text="MV 影片", width=10, bootstyle="secondary").pack(side="left")
        self._video_var = tk.StringVar()
        ttkb.Entry(row, textvariable=self._video_var, width=52).pack(
            side="left", fill="x", expand=True, padx=theme.SPACE)
        ttkb.Button(row, text="選擇…", bootstyle="secondary", takefocus=False,
                    command=self._pick_video).pack(side="left", padx=2)
        ttkb.Button(row, text="清除", bootstyle="secondary", takefocus=False,
                    command=self._clear_video).pack(side="left")

        self._video_info_var = tk.StringVar(value="未選擇影片")
        ttkb.Label(box, textvariable=self._video_info_var, bootstyle="info",
                   font=(theme.mono_font_family(), 9)) \
            .pack(anchor="w", padx=theme.SPACE + 76, pady=2)

        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=2)
        ttkb.Label(row, text="語言提示", width=10, bootstyle="secondary").pack(side="left")
        self._lang_var = tk.StringVar(value="zh")
        # ⚠️ ADR-001：yue 係「粵語→普通話」開關，UI 直接畀 zh（內部照樣重映射防呆）
        ttkb.Radiobutton(row, text="粵語 / 中文", variable=self._lang_var, value="zh") \
            .pack(side="left", padx=(0, theme.SPACE))
        ttkb.Radiobutton(row, text="英文", variable=self._lang_var, value="en") \
            .pack(side="left")

        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(2, theme.SPACE))
        ttkb.Label(row, text="輸出資料夾", width=10, bootstyle="secondary").pack(side="left")
        self._output_var = tk.StringVar()
        ttkb.Entry(row, textvariable=self._output_var, width=52).pack(
            side="left", fill="x", expand=True, padx=theme.SPACE)
        ttkb.Button(row, text="選擇…", bootstyle="secondary", takefocus=False,
                    command=self._pick_output).pack(side="left", padx=2)
        ttkb.Button(row, text="用預設", bootstyle="secondary", takefocus=False,
                    command=lambda: self._output_var.set("")).pack(side="left")

        # 「開啟現有」路徑（B1 決定：直接揀 *_timings.json 跳編輯器，唔使重跑 ASR）
        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(0, theme.SPACE))
        ttkb.Button(row, text="📂 開啟現有 *_timings.json…", bootstyle="secondary",
                    takefocus=False, command=self._open_existing).pack(side="left")

    # ------------------------------------------------------------------
    # 左欄 · 樣式
    # ------------------------------------------------------------------

    def _build_style(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="樣式", bootstyle="light")
        box.pack(fill="x", pady=(0, theme.SPACE))

        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(theme.SPACE, 2))
        ttkb.Label(row, text="字體", width=10, bootstyle="secondary").pack(side="left")
        families = self._font_families()
        saved_font = str(self._saved_style.get("font_name") or "")
        if saved_font not in families:
            if saved_font:
                LOGGER.info("儲存的字型 %r 未安裝，自動改用可用字型", saved_font)
            saved_font = self._pick_font(families)
        self._font_var = tk.StringVar(value=saved_font)
        self._font_combo = ttkb.Combobox(row, textvariable=self._font_var,
                                         values=families, width=26)
        self._font_combo.pack(side="left", padx=(0, theme.SPACE))
        self._font_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        self._font_combo.bind("<KeyRelease>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="字級", bootstyle="secondary").pack(side="left")
        self._size_var = tk.IntVar(value=self._int_saved("font_size", 48))
        spin = ttkb.Spinbox(row, textvariable=self._size_var, from_=8, to=300,
                            increment=1, width=6, command=self._refresh_preview)
        spin.pack(side="left", padx=4)
        spin.bind("<KeyRelease>", lambda _e: self._refresh_preview())

        # 三色（唱過 / 未唱 / 描邊）：色塊 + hex 輸入
        self._color_vars: dict[str, tk.StringVar] = {}
        self._swatches: dict[str, tk.Canvas] = {}
        colors = [("primary", "唱過色", theme.SUB_PRIMARY),
                  ("secondary", "未唱色", theme.SUB_SECONDARY),
                  ("outline", "描邊色", "#000000")]
        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=2)
        for key, label, default in colors:
            cell = ttkb.Frame(row, bootstyle="light")
            cell.pack(side="left", padx=(0, 12))
            # grid（唔用 pack）：視窗窄嘅時候 pack 會先擠細第一個子元件，
            # 令色塊縮到睇唔到／被輸入框蓋住 —— 用戶回報「色塊同選取色唔一致」嘅根源。
            ttkb.Label(cell, text=label, bootstyle="secondary") \
                .grid(row=0, column=0, columnspan=2, sticky="w")
            cur = str(self._saved_style.get(f"{key}_color") or default)
            swatch = tk.Canvas(cell, width=30, height=22, highlightthickness=1,
                               highlightbackground=theme.BORDER, cursor="hand2")
            swatch.grid(row=1, column=0, sticky="w")
            swatch.bind("<Button-1>", lambda _e, k=key: self._pick_color(k))
            self._swatches[key] = swatch
            var = tk.StringVar(value=cur)
            var.trace_add("write", lambda *_a, k=key: self._on_hex_changed(k))
            entry = ttkb.Entry(cell, textvariable=var, width=9)
            entry.grid(row=1, column=1, padx=(4, 0), sticky="w")
            # 離焦／Enter 就把輸入框正規化成 #RRGGBB（唔會出現 "ff8800" 同色塊唔一致）
            entry.bind("<FocusOut>", lambda _e, k=key: self._normalise_entry(k))
            entry.bind("<Return>", lambda _e, k=key: self._normalise_entry(k))
            self._color_vars[key] = var
            # 初始就畫一次（唔靠 trace：初始值唔會觸發）
            self._set_swatch(key, _normalise_hex(cur) or default)

        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=2)
        ttkb.Label(row, text="描邊粗幼", width=10, bootstyle="secondary").pack(side="left")
        self._outline_var = tk.StringVar(
            value=_OUTLINE_NAME.get(
                self._float_saved("outline_width", 0.0), _OUTLINE_WIDTHS[0][0]))
        combo = ttkb.Combobox(row, textvariable=self._outline_var,
                              values=[n for n, _ in _OUTLINE_WIDTHS], width=8,
                              state="readonly")
        combo.pack(side="left", padx=(0, theme.SPACE))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="對齊", bootstyle="secondary").pack(side="left")
        self._align_var = tk.StringVar(
            value=_ALIGN_NAME.get(self._int_saved("alignment", 2), _ALIGNMENTS[0][0]))
        combo = ttkb.Combobox(row, textvariable=self._align_var,
                              values=[n for n, _ in _ALIGNMENTS], width=10,
                              state="readonly")
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())

        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(2, 2))
        ttkb.Label(row, text="底部留白", width=10, bootstyle="secondary").pack(side="left")
        self._margin_var = tk.IntVar(value=self._int_saved("margin_v", 0))
        scale = ttkb.Scale(row, from_=0, to=400, variable=self._margin_var,
                           command=lambda _v: self._refresh_preview(), length=200)
        scale.pack(side="left", padx=4)
        self._margin_label = ttkb.Label(row, text="0 = 自動（1080p ≈ 59px）",
                                        bootstyle="secondary",
                                        font=(theme.mono_font_family(), 9))
        self._margin_label.pack(side="left", padx=theme.SPACE)

        # ADR-027：雙行歌詞（奇數句上行、偶數句下行，每行隔兩句才換）
        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(0, 2))
        self._dual_var = tk.BooleanVar(value=bool(self._saved_style.get("dual_line", False)))
        ttkb.Checkbutton(row, text="雙行歌詞", variable=self._dual_var,
                         bootstyle="primary-round-toggle",
                         command=self._refresh_preview).pack(side="left")
        ttkb.Label(row, text="換行延遲", bootstyle="secondary").pack(
            side="left", padx=(theme.SPACE, 2))
        self._delay_var = tk.IntVar(value=self._int_saved("switch_delay_ms", 1000))
        delay_spin = ttkb.Spinbox(row, textvariable=self._delay_var, from_=0, to=3000,
                                  increment=100, width=6, command=self._refresh_preview)
        delay_spin.pack(side="left")
        delay_spin.bind("<KeyRelease>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="ms", bootstyle="secondary").pack(side="left", padx=(2, 0))

        # ADR-030：上下行水平錯位（原片效果 = 上偏左、下偏右）
        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(0, 2))
        ttkb.Label(row, text="上行位置", width=8, bootstyle="secondary").pack(side="left")
        self._up_pos_var = tk.StringVar(
            value=_POS_NAME.get(self._int_saved("up_offset_pct", 0), "居中"))
        up_combo = ttkb.Combobox(row, textvariable=self._up_pos_var, state="readonly",
                                 values=[n for n, _ in _POSITIONS], width=6)
        up_combo.pack(side="left")
        up_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="下行位置", width=8, bootstyle="secondary").pack(
            side="left", padx=(theme.SPACE, 0))
        self._down_pos_var = tk.StringVar(
            value=_POS_NAME.get(self._int_saved("down_offset_pct", 0), "居中"))
        down_combo = ttkb.Combobox(row, textvariable=self._down_pos_var, state="readonly",
                                   values=[n for n, _ in _POSITIONS], width=6)
        down_combo.pack(side="left")
        down_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())

        # ADR-029：整體時間平移（換另一版剪輯的 MV 時微調；只影響 render）
        row = ttkb.Frame(box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(0, theme.SPACE))
        ttkb.Label(row, text="整體平移", width=10, bootstyle="secondary").pack(side="left")
        self._shift_var = tk.IntVar(value=self._int_saved("shift_ms", 0))
        shift_spin = ttkb.Spinbox(row, textvariable=self._shift_var, from_=-5000, to=5000,
                                  increment=50, width=7, command=self._refresh_preview)
        shift_spin.pack(side="left", padx=4)
        shift_spin.bind("<KeyRelease>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="ms（正值 = 全部延後；唔會改 timings 原始時間）",
                   bootstyle="secondary", font=(theme.ui_font_family(), 8)).pack(side="left")

    # ------------------------------------------------------------------
    # 左欄 · 進階（預設收埋，B2）
    # ------------------------------------------------------------------

    def _build_advanced(self, parent) -> None:
        self._adv_open = False
        self._adv_btn = ttkb.Button(parent, text="▸ 進階", bootstyle="secondary",
                                    takefocus=False, command=self._toggle_advanced)
        self._adv_btn.pack(anchor="w", pady=(0, 2))

        self._adv_box = ttkb.LabelFrame(parent, text="進階", bootstyle="light")
        # 唔 pack，摺疊狀態

        grid = ttkb.Frame(self._adv_box, bootstyle="light")
        grid.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)

        self._keep_punct_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(grid, text="保留標點（--keep-punctuation）",
                         variable=self._keep_punct_var, bootstyle="primary-round-toggle") \
            .grid(row=0, column=0, columnspan=2, sticky="w")

        self._adv_spins: dict[str, tk.StringVar] = {}
        fields = [
            ("每行最多字數", "max_chars", "18", 2, 60),
            ("停頓閾值 (ms)", "gap_threshold_ms", "800", 100, 5000),
            ("短句門檻 (字)", "min_clause_chars", "5", 1, 20),
            ("短句門檻 (ms)", "min_clause_ms", "2500", 0, 10000),
            ("合併上限 (字)", "merge_max_chars", "16", 4, 60),
            ("句尾停留 (ms)", "hold_ms", "300", 0, 2000),
        ]
        for i, (label, key, default, lo, hi) in enumerate(fields):
            r, c = 1 + i // 2, (i % 2) * 3
            ttkb.Label(grid, text=label, bootstyle="secondary").grid(row=r, column=c, sticky="w", padx=(0, 2))
            var = tk.StringVar(value=default)
            ttkb.Spinbox(grid, textvariable=var, from_=lo, to=hi, width=7) \
                .grid(row=r, column=c + 1, sticky="w", padx=(0, theme.SPACE))
            self._adv_spins[key] = var

        self._separate_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(grid, text="人聲分離（首次需下載模型）",
                         variable=self._separate_var, bootstyle="primary-round-toggle") \
            .grid(row=4, column=0, columnspan=2, sticky="w")
        self._save_raw_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(grid, text="儲存 ASR 原始 JSON", variable=self._save_raw_var,
                         bootstyle="primary-round-toggle").grid(row=4, column=3, columnspan=2, sticky="w")

        row = ttkb.Frame(self._adv_box, bootstyle="light")
        row.pack(fill="x", padx=theme.SPACE, pady=(0, theme.SPACE))
        ttkb.Label(row, text="ASR 模型", bootstyle="secondary").pack(side="left")
        self._model_var = tk.StringVar(value="qwen-audio-3.1-asr-flash-filetrans")
        ttkb.Combobox(row, textvariable=self._model_var, width=36,
                      values=["qwen-audio-3.1-asr-flash-filetrans"]).pack(side="left", padx=4)

    def _toggle_advanced(self) -> None:
        self._adv_open = not self._adv_open
        if self._adv_open:
            self._adv_box.pack(fill="x", pady=(0, theme.SPACE))
            self._adv_btn.configure(text="▾ 進階")
        else:
            self._adv_box.pack_forget()
            self._adv_btn.configure(text="▸ 進階")

    # ------------------------------------------------------------------
    # 右欄
    # ------------------------------------------------------------------

    def _build_right(self, parent) -> None:
        # ⚠️ 進度框必須**最先** pack（side="bottom"）：Tk 空間不足時會裁掉最後
        #    pack 的 widget，而右欄冇 scroll。用戶回報「生成／燒錄冇進度條」，
        #    根因之一就係進度框被裁走（同 burn_dialog.py 踩過嘅坑一樣）。
        prog_box = ttkb.LabelFrame(parent, text="進度", bootstyle="light")
        prog_box.pack(side="bottom", fill="x")

        box = ttkb.LabelFrame(parent, text="即時預覽", bootstyle="light")
        box.pack(fill="x", pady=(0, theme.SPACE))
        self._preview = StylePreviewCanvas(box)
        self._preview.pack(padx=theme.SPACE, pady=theme.SPACE)
        legend = ttkb.Frame(box, bootstyle="light")
        legend.pack(padx=theme.SPACE, pady=(0, theme.SPACE))
        self._legend_swatches: list[tk.Canvas] = []
        for color, label in ((theme.SUB_PRIMARY, "唱過"), (theme.SUB_SECONDARY, "未唱"),
                             ("#000000", "描邊")):
            chip = tk.Canvas(legend, width=12, height=12, bg=color,
                             highlightthickness=1, highlightbackground=theme.BORDER)
            chip.pack(side="left")
            self._legend_swatches.append(chip)
            ttkb.Label(legend, text=label, bootstyle="secondary").pack(side="left", padx=(2, theme.SPACE))
        ttkb.Label(box, text="預覽畫布 344×180 · 系統字體渲染，唔係 libass 逐像素預覽",
                   bootstyle="secondary",
                   font=(theme.ui_font_family(), 8)).pack(padx=theme.SPACE, pady=(0, theme.SPACE))

        box = ttkb.LabelFrame(parent, text="產出", bootstyle="light")
        box.pack(fill="x", pady=(0, theme.SPACE))
        ttkb.Label(box, text="<片名>_timings.json　canonical（UI 編輯呢個）", bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(anchor="w", padx=theme.SPACE)
        ttkb.Label(box, text="<片名>_karaoke.ass　　衍生物（render 重出）", bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(anchor="w", padx=theme.SPACE)
        ttkb.Label(box, text="<片名>_karaoke.mp4　　需另行燒錄（burn）", bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(anchor="w", padx=theme.SPACE)
        ttkb.Label(box, text="⚠ 唔可以直接改 .ass —— 下次 render 會無條件覆寫",
                   bootstyle="danger", font=(theme.ui_font_family(), 8)) \
            .pack(anchor="w", padx=theme.SPACE, pady=(4, theme.SPACE))

        self._generate_btn = ttkb.Button(parent, text="▶  開始生成", bootstyle=theme.ACCENT,
                                         takefocus=False, command=self._start_generate)
        self._generate_btn.pack(fill="x", ipady=8, pady=(0, 2))
        # 取消按鈕：預設隱藏，生成期間才出現（ADR-025）。按鈕唔用 disabled 的
        # 「開始生成」兼職取消 —— 誤按代價太大（辛苦跑的 ASR 任務會被中止）。
        self._cancel_btn = ttkb.Button(parent, text="✖  取消", bootstyle="danger-outline",
                                       takefocus=False, command=self._cancel_generate)
        ttkb.Button(parent, text="⧉ 複製等價 CLI 指令", bootstyle="secondary-link",
                    takefocus=False, command=self._copy_cli).pack(fill="x", pady=(0, theme.SPACE))

        self._progress = StageProgress(prog_box)
        self._progress.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)

    def restyle(self) -> None:
        """換主題：accent 按鈕、顏色色塊、圖例小方塊重新上色。"""
        try:
            self._generate_btn.configure(bootstyle=theme.ACCENT)
        except Exception:      # noqa: BLE001
            pass
        for key in list(self._swatches):
            self._set_swatch(key, _normalise_hex(self._color_vars[key].get()))
        for chip in getattr(self, "_legend_swatches", []):
            try:
                chip.configure(highlightbackground=theme.BORDER)
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _font_families(self) -> list[str]:
        """系統字型清單：**中文字型排前**（ADR-030）。

        tkinter 會列出全部已安裝字型（幾百個），英文體在前會令用戶以為「冇中文
        字型可揀」。按常見中文字型關鍵字分成兩組，中文組排前並按名排序。
        """
        from ..widgets.style_preview import tk_font_families_safe

        families = tk_font_families_safe() or [_DEFAULT_FONT]

        def _is_cjk(name: str) -> bool:
            low = name.lower()
            return any(hint in low for hint in _CJK_FONT_HINTS)

        cjk = sorted(f for f in families if _is_cjk(f))
        other = sorted(f for f in families if not _is_cjk(f))
        return cjk + other

    @staticmethod
    def _pick_font(families: list[str]) -> str:
        """由優先序揀第一個**實際存在**的字型；全部冇就退第一個。

        ⚠️ 實測：Windows 只裝了 `Microsoft JhengHei UI`，冇 `Microsoft JhengHei`
        本體 —— 寫死名字會令 libass 靜默 fallback 去別的字型（用戶見到「揀了都
        唔似」）。所以初始值一定要對照系統清單。
        """
        if not families:
            return _DEFAULT_FONT
        for name in _FONT_PREFERENCE:
            if name in families:
                return name
        return families[0]

    # ------------------------------------------------------------------
    # 樣式狀態：snapshot / apply（ADR-015：主頁 = 燒錄權威來源）
    # ------------------------------------------------------------------

    def _int_saved(self, key: str, default: int) -> int:
        try:
            return int(self._saved_style.get(key))
        except (TypeError, ValueError):
            return default

    def _float_saved(self, key: str, default: float) -> float:
        try:
            return float(self._saved_style.get(key))
        except (TypeError, ValueError):
            return default

    def style_snapshot(self) -> dict[str, Any]:
        """主頁樣式面板當前值（dict；燒錄 / 生成 / 持久化共用）。

        Returns:
            ``font_name`` / ``font_size`` / ``primary_color`` / ``secondary_color`` /
            ``outline_color`` / ``alignment`` / ``margin_v`` / ``outline_width``。
            **唔含** ``play_res_x/y`` —— 座標系要由原片（ADR-006）決定。
        """
        try:
            font_size = max(8, int(self._size_var.get()))
        except (tk.TclError, ValueError):
            font_size = 48
        try:
            margin_v = max(0, int(self._margin_var.get()))
        except (tk.TclError, ValueError):
            margin_v = 0
        try:
            delay_ms = max(0, int(self._delay_var.get()))
        except (tk.TclError, ValueError):
            delay_ms = 1000
        try:
            shift_ms = int(self._shift_var.get())
        except (tk.TclError, ValueError):
            shift_ms = 0
        return {
            "font_name": self._font_var.get().strip() or _DEFAULT_FONT,
            "font_size": font_size,
            "primary_color": self._color_vars["primary"].get().strip(),
            "secondary_color": self._color_vars["secondary"].get().strip(),
            "outline_color": self._color_vars["outline"].get().strip(),
            "alignment": dict(_ALIGNMENTS).get(self._align_var.get(), 2),
            "margin_v": margin_v,
            "outline_width": self._outline_width_value(),
            # ADR-027：雙行歌詞（舊 settings.json 冇呢兩個 key → 自動單行）
            "dual_line": bool(self._dual_var.get()),
            "switch_delay_ms": delay_ms,
            # ADR-029：整體平移（正值 = 延後）
            "shift_ms": shift_ms,
            # ADR-030：上下行水平錯位
            "up_offset_pct": dict(_POSITIONS).get(self._up_pos_var.get(), 0),
            "down_offset_pct": dict(_POSITIONS).get(self._down_pos_var.get(), 0),
        }

    def apply_style(self, style: dict[str, Any]) -> None:
        """把 ``style``（timings.json 的 ``style`` 段）載入面板。

        用途：開啟舊檔 / 編輯器 🎨 改色後，令主頁面板同實際燒錄樣式一致，
        避免「主頁顯示 48px 黃字、燒錄出 72px 藍字」嘅落差。

        Args:
            style: 至少含 ``font_name`` 等欄位；缺欄位就保留現值。
        """
        if not style:
            return
        font = str(style.get("font_name") or "").strip()
        if font:
            if font in self._font_families():
                self._font_var.set(font)
            else:
                LOGGER.warning("timings.json 的字型 %r 未安裝，沿用目前字型", font)
        for key, var in self._color_vars.items():
            value = str(style.get(f"{key}_color") or "").strip()
            if len(value) == 6:
                value = "#" + value
            if len(value) == 7:
                try:
                    int(value[1:], 16)
                except ValueError:
                    continue
                var.set(value.upper())
        if style.get("font_size"):
            self._size_var.set(int(style["font_size"]))
        if style.get("margin_v") is not None:
            self._margin_var.set(int(style.get("margin_v") or 0))
        alignment = int(style.get("alignment") or 2)
        self._align_var.set(_ALIGN_NAME.get(alignment, _ALIGNMENTS[0][0]))
        outline = float(style.get("outline_width") or 0.0)
        # 對唔上預設級距（例如人手改過 JSON）就直接顯示 px 值，唔好靜靜當「自動」
        self._outline_var.set(_OUTLINE_NAME.get(outline, f"{outline:g}"))
        # ADR-027：舊檔冇呢兩個 key → 保持單行、延遲沿用預設
        if "dual_line" in style:
            self._dual_var.set(bool(style.get("dual_line")))
        if style.get("switch_delay_ms") is not None:
            self._delay_var.set(int(style.get("switch_delay_ms") or 1000))
        if style.get("shift_ms") is not None:
            self._shift_var.set(int(style.get("shift_ms") or 0))
        self._up_pos_var.set(_POS_NAME.get(int(style.get("up_offset_pct") or 0), "居中"))
        self._down_pos_var.set(_POS_NAME.get(int(style.get("down_offset_pct") or 0), "居中"))
        self._refresh_preview()

    def _schedule_style_save(self) -> None:
        """樣式變更 → 600ms debounce 後寫 settings.json（拖滑桿唔會瘋狂寫檔）。"""
        if self._style_save_job is not None:
            try:
                self.after_cancel(self._style_save_job)
            except (tk.TclError, ValueError):
                pass
        self._style_save_job = self.after(600, self._save_style)

    def _save_style(self) -> None:
        self._style_save_job = None
        snapshot = self.style_snapshot()
        if snapshot == self._saved_style:
            return
        self._saved_style = snapshot
        data = dict(self.master.settings_data)
        data["home_style"] = snapshot
        self.master.save_settings(data)
        # 主頁 = 燒錄權威 → 有開檔就即刻反映去編輯器預覽，避免「預覽舊樣式、
        # 燒錄新樣式」嘅落差
        getter = getattr(self.master, "page", None)
        editor = getter("editor") if callable(getter) else None
        if editor is not None and hasattr(editor, "on_home_style_changed"):
            try:
                editor.on_home_style_changed(snapshot)
            except Exception:   # noqa: BLE001 — 同步失敗唔應該阻住存設定
                LOGGER.exception("同步樣式去編輯器失敗")

    def _pick_video(self) -> None:
        start = self.master.settings_data.get("default_input_dir") or ""
        path = filedialog.askopenfilename(title="揀 MV 影片", initialdir=start or None,
                                          filetypes=VIDEO_PATTERNS)
        if not path:
            return
        self._video_var.set(path)
        self._probe_video(Path(path))

    def _clear_video(self) -> None:
        self._video_var.set("")
        self._video_info_var.set("未選擇影片")
        self._video_path = None
        self._video_info = {}

    def _pick_output(self) -> None:
        start = self._output_var.get() or \
            self.master.settings_data.get("default_output_dir") or ""
        path = filedialog.askdirectory(title="揀輸出資料夾", initialdir=start or None)
        if path:
            self._output_var.set(path)

    def _pick_color(self, key: str) -> None:
        rgb = colorchooser.askcolor(color=self._swatches[key]["bg"], parent=self)
        if rgb and rgb[1]:
            hex_value = "#{:02X}{:02X}{:02X}".format(*[round(v) for v in rgb[0]])
            self._color_vars[key].set(hex_value)
            self._set_swatch(key, hex_value)   # 唔等 trace，即刻上色

    def _set_swatch(self, key: str, color: str | None) -> None:
        """把色塊畫成 `color`；``color=None``（輸入未合法）就保留舊色 + 紅框提示。

        色塊用 **Canvas 背景 + 一個填滿嘅 rectangle item** 雙重上色：
        單純寫 `["bg"]` 喺某些 ttk 狀態／被擠壓時睇唔出變化，令用戶以為
        「色塊同揀嘅色唔一致」（實際回報）。rectangle 係圖形 item，一定畫得出。

        Args:
            key: ``primary`` / ``secondary`` / ``outline``。
            color: 正規化後嘅 ``#RRGGBB``；``None`` = 保留現色但標紅。
        """
        swatch = self._swatches.get(key)
        if swatch is None:
            return
        if color is None:
            swatch.configure(highlightbackground=theme.DANGER,
                             highlightcolor=theme.DANGER)
            return
        w = int(swatch["width"]) + 2 * int(swatch["highlightthickness"])
        h = int(swatch["height"]) + 2 * int(swatch["highlightthickness"])
        swatch.configure(bg=color, highlightbackground=theme.BORDER,
                         highlightcolor=theme.BORDER)
        swatch.delete("all")
        swatch.create_rectangle(0, 0, w, h, fill=color, outline=color, tags="fill")

    def _normalise_entry(self, key: str) -> None:
        """輸入框失焦／Enter：把 ``ff8800`` 之類寫返做 ``#FF8800``（同色塊一致）。"""
        var = self._color_vars.get(key)
        if var is None:
            return
        color = _normalise_hex(var.get())
        if color and color != var.get():
            var.set(color)

    def _on_hex_changed(self, key: str) -> None:
        """輸入框內容變 → 同步色塊（合法色即時上色，未打完就標紅但保留舊色）。"""
        self._set_swatch(key, _normalise_hex(self._color_vars[key].get()))
        self._refresh_preview()

    def _probe_video(self, video: Path) -> None:
        """背景 ffprobe 讀解像度 / 時長（計劃書 §5.2 步驟 1）。"""
        self._video_info_var.set("讀取影片資訊中…")
        def worker() -> None:
            import main
            ffprobe = settings.detect_ffprobe(self.master.settings_data)
            if not ffprobe:
                self.after(0, lambda: self._video_info_var.set("⚠ 搵唔到 ffprobe（設定頁填路徑）"))
                return
            w, h = main.probe_video_size(ffprobe, video)
            try:
                proc = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(video)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
                duration = float((proc.stdout or "0").strip() or 0)
            except ValueError:
                duration = 0.0
            codec = main.probe_audio_codec(ffprobe, video)
            info = {"w": w, "h": h, "duration": duration, "codec": codec}
            self.after(0, lambda: self._show_video_info(video, info))
        threading.Thread(target=worker, daemon=True).start()

    def _show_video_info(self, video: Path, info: dict[str, Any]) -> None:
        self._video_path = video
        self._video_info = info
        minutes, seconds = divmod(info["duration"], 60)
        self._video_info_var.set(
            f"● {info['w']}×{info['h']} · {int(minutes):02d}:{seconds:05.2f}"
            f" · 音軌 {info['codec'] or '冇'}")
        if not self._output_var.get():
            self._output_var.set(str(video.parent))

    def _open_existing(self) -> None:
        path = filedialog.askopenfilename(
            title="揀 *_timings.json",
            filetypes=[("timings JSON", "*_timings.json"), ("全部檔案", "*.*")])
        if path:
            self.master.open_editor_with(path)

    def _refresh_preview(self) -> None:
        try:
            size = int(self._size_var.get())
        except (tk.TclError, ValueError):
            size = 48
        try:
            margin = int(self._margin_var.get())
        except (tk.TclError, ValueError):
            margin = 0
        self._margin_label.configure(
            text=f"{margin} = 自動（1080p ≈ 59px）" if margin == 0 else f"{margin}px")
        self._preview.set_style(
            font_name=self._font_var.get(), font_size=size,
            primary_color=self._color_vars["primary"].get(),
            secondary_color=self._color_vars["secondary"].get(),
            outline_color=self._color_vars["outline"].get(),
            outline_width=self._outline_width_value(),
            margin_v=margin,
            dual_line=bool(self._dual_var.get()),
            alignment=dict(_ALIGNMENTS).get(self._align_var.get(), 2),
            up_offset_pct=dict(_POSITIONS).get(self._up_pos_var.get(), 0),
            down_offset_pct=dict(_POSITIONS).get(self._down_pos_var.get(), 0))
        self._schedule_style_save()

    def _outline_width_value(self) -> float:
        """「自動 / 幼 2 / 中 3 / 粗 4」→ px；非預設級距（例如 JSON 寫 3.5）照 parse。"""
        name = self._outline_var.get().strip()
        mapping = dict(_OUTLINE_WIDTHS)
        if name in mapping:
            return mapping[name]
        try:
            return max(0.0, float(name))
        except ValueError:
            return 0.0

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def _build_options(self) -> "main.GenerateOptions | None":  # type: ignore[name-defined]
        import main
        video = Path(self._video_var.get().strip())
        if not video.is_file():
            messagebox.showwarning("未選擇影片", "請先揀一條 MV 影片。", parent=self)
            return None
        try:
            snap = self.style_snapshot()
            style = main.StyleConfig(
                font_name=snap["font_name"],
                font_size=snap["font_size"],
                primary_color=snap["primary_color"],
                secondary_color=snap["secondary_color"],
                outline_color=snap["outline_color"],
                # 之前漏咗呢兩個 → 主頁「對齊」「描邊粗幼」揀咩都冇效
                alignment=snap["alignment"],
                outline_width=snap["outline_width"],
                margin_v=snap["margin_v"],
                # ADR-027：雙行歌詞開關
                dual_line=bool(snap.get("dual_line", False)),
                switch_delay_ms=int(snap.get("switch_delay_ms") or 1000),
                # ADR-029：整體平移
                shift_ms=int(snap.get("shift_ms") or 0),
                # ADR-030：上下行水平錯位
                up_offset_pct=int(snap.get("up_offset_pct") or 0),
                down_offset_pct=int(snap.get("down_offset_pct") or 0),
            )
        except (ValueError, tk.TclError):
            messagebox.showerror("樣式參數錯誤", "字級 / 留白要係整數。", parent=self)
            return None
        # API key 優先序（同設定頁一致）：env > .env > settings.json
        # ⚠️ main.resolve_api_key 淨係識讀 env/.env，settings.json 嗰個要 UI 傳入
        from .. import settings as ui_settings
        api_key, _key_source = ui_settings.resolve_api_key(self.master.settings_data)
        return main.GenerateOptions(
            input=video,
            output_dir=Path(self._output_var.get().strip()) if self._output_var.get().strip() else None,
            model=self._model_var.get().strip(),
            language=self._lang_var.get(),
            api_key=api_key or None,
            separate_vocals=self._separate_var.get(),
            max_chars=self._adv_int("max_chars", 18),
            gap_threshold_ms=self._adv_int("gap_threshold_ms", 800),
            hold_ms=self._adv_int("hold_ms", 300),
            min_clause_chars=self._adv_int("min_clause_chars", 5),
            min_clause_ms=self._adv_int("min_clause_ms", 2500),
            merge_max_chars=self._adv_int("merge_max_chars", 16),
            keep_punctuation=self._keep_punct_var.get(),
            save_raw=self._save_raw_var.get(),
            style=style,
        )

    def _start_generate(self) -> None:
        import main

        if self._flight is None:
            self._flight = main.SingleFlight()
        if not self._flight.acquire():
            messagebox.showinfo("進行中", "已經有任務跑緊（可按「✖ 取消」中止）。", parent=self)
            return

        # `_build_options` 內部已處理預期錯誤（缺片、樣式參數），但這裡再包一層
        # 兜底：Tk callback 內任何未捕捉例外都只會去 stderr，用戶見到「按鈕冇反應」。
        try:
            opts = self._build_options()
        except Exception as exc:      # noqa: BLE001
            self._flight.release()
            messagebox.showerror("參數錯誤", f"無法開始生成：{exc}", parent=self)
            return
        if opts is None:
            self._flight.release()
            return

        self._cancel_event = opts.cancel_event
        self._generate_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal", text="✖  取消")
        self._cancel_btn.pack(fill="x", ipady=4, pady=(0, 2))
        self._progress.reset()

        # 攔截 karaoke logger → UI log 窗（計劃書 §5.2 步驟 3）
        handler = QueueLogHandler(self._progress)
        logging.getLogger("karaoke").addHandler(handler)

        def on_error(exc: BaseException) -> None:
            self._progress.fail(f"失敗：{exc}")
            messagebox.showerror("生成失敗", str(exc), parent=self)

        def worker() -> None:
            import main
            try:
                result = main.generate_timings(opts, progress_cb=self._progress.report,
                                               percent_cb=self._progress.set_fraction)
            except main.TaskCancelled:
                # 取消唔係錯誤 → 唔彈 error dialog，只標記狀態（ADR-025）
                self._progress.fail("已取消（未產生新檔）")
            except SystemExit as exc:
                # 保險層：ADR-024 起 main 改拋 KaraokeError（會落到下面的 Exception
                # 分支並顯示可讀訊息），這條只接第三方套件殘留的 sys.exit。
                # ⚠️ Python 3 喺 except block 結束時刪除 exc，lambda 延遲執行會 NameError
                # → 必須即刻捕獲值（default arg 綁定）
                code = exc.code
                self.after(0, lambda code=code: on_error(
                    RuntimeError(f"exit code {code}，詳見 log")))
            except Exception as exc:       # noqa: BLE001
                err = exc
                self.after(0, lambda err=err: on_error(err))
            else:
                self._progress.succeed("完成 ✅")
                self.after(0, lambda: self._on_generate_done(result))
            finally:
                logging.getLogger("karaoke").removeHandler(handler)
                self.after(0, self._reset_after_generate)

        threading.Thread(target=worker, daemon=True).start()

    def _cancel_generate(self) -> None:
        """送出取消訊號（ADR-025）。

        取消係「請求」而唔係「即刻停」：ASR 正在輪詢、ffmpeg 正在燒錄都要等下一個
        檢查點。所以按鈕即刻變 disabled 並顯示「取消中…」，避免用戶連按。
        """
        event = self._cancel_event
        if event is None:
            return
        event.set()
        self._cancel_btn.configure(state="disabled", text="取消中…")
        # 用 log 而唔用 report：report 的 stage 必須在 STAGES 內，
        # 「取消」不是一個階段（進度燈應該停在當前階段，唔應該跳格）。
        self._progress.log("已送出取消訊號，等待目前步驟結束 …")

    def _reset_after_generate(self) -> None:
        """worker 結束後復原 UI（**必須**在任何路徑執行，否則按鈕永久鎖死）。"""
        self._generate_btn.configure(state="normal")
        self._cancel_btn.pack_forget()
        self._cancel_btn.configure(state="normal", text="✖  取消")
        self._cancel_event = None
        if self._flight is not None:
            self._flight.release()

    def _copy_cli(self) -> None:
        """「複製等價 CLI 指令」（§5.5.5）：由目前 UI 值動態生成 generate 指令。"""
        import tkinter.messagebox as messagebox
        from ..clidoc import generate_cli_command
        video = self._video_var.get().strip() or "C:\\Videos\\MV.mp4"
        values: dict[str, Any] = {
            "input": video,
            "model": self._model_var.get().strip(),
            "language": self._lang_var.get(),
            "separate_vocals": self._separate_var.get(),
            "max_chars": self._safe_int(self._adv_spins["max_chars"].get(), 18),
            "gap_threshold_ms": self._safe_int(self._adv_spins["gap_threshold_ms"].get(), 800),
            "hold_ms": self._safe_int(self._adv_spins["hold_ms"].get(), 300),
            "min_clause_chars": self._safe_int(self._adv_spins["min_clause_chars"].get(), 5),
            "min_clause_ms": self._safe_int(self._adv_spins["min_clause_ms"].get(), 2500),
            "merge_max_chars": self._safe_int(self._adv_spins["merge_max_chars"].get(), 16),
            "keep_punctuation": self._keep_punct_var.get(),
            "save_raw": self._save_raw_var.get(),
            "font_name": self._font_var.get().strip(),
            "font_size": self._safe_int(str(self._size_var.get()), 48),
            "primary_color": self._color_vars["primary"].get().strip(),
            "secondary_color": self._color_vars["secondary"].get().strip(),
            "outline_color": self._color_vars["outline"].get().strip(),
            "margin_v": self._safe_int(str(self._margin_var.get()), 0),
        }
        snap = self.style_snapshot()
        values["alignment"] = snap["alignment"]
        values["outline_width"] = snap["outline_width"]
        if self._output_var.get().strip():
            values["output_dir"] = self._output_var.get().strip()
        cmd = generate_cli_command("generate", values)
        self.clipboard_clear()
        self.clipboard_append(cmd)
        messagebox.showinfo("已複製", f"已複製到剪貼簿：\n\n{cmd}", parent=self)

    @staticmethod
    def _safe_int(text: str, default: int) -> int:
        try:
            return int(text)
        except (TypeError, ValueError):
            return default

    def _adv_int(self, key: str, default: int) -> int:
        """讀「進階」面板某個 Spinbox 的整數值，唔合法就用預設（§2.5 P0）。

        為何要防呆：Spinbox 可以被清空（Ctrl+A + Delete）或貼上非數字，``int("")``
        會拋 ``ValueError``。以前這個例外發生在 Tk command callback 內，tkinter
        只把它印到 stderr —— 用戶按了「開始生成」，畫面完全冇反應、也冇錯誤訊息。

        旁邊的 :meth:`_copy_cli` 一早用了 ``_safe_int``，同一批欄位兩套標準；
        現在統一走這裡。

        Args:
            key: ``self._adv_spins`` 的欄位名。
            default: 解析失敗時的預設值（與 CLI 預設一致）。

        Returns:
            整數值。
        """
        spin = self._adv_spins.get(key)
        if spin is None:
            return default
        try:
            return self._safe_int(spin.get(), default)
        except tk.TclError:      # 視窗已銷毀
            return default

    def _on_generate_done(self, result) -> None:
        """完成對話框：列出 3 個產出 + 「立即開啟編輯器」預設動作（計劃書 §5.2 步驟 4）。"""
        box = messagebox.askyesno(
            "完成 ✅",
            f"timings : {result.json_path}\n"
            f"ass     : {result.ass_path}\n\n"
            f"{result.num_lines} 行 / {result.num_chars} 字 / "
            f"{result.num_estimated_words} 個估算詞\n\n"
            f"立即開啟編輯器？",
            parent=self)
        if box:
            self.master.open_editor_with(result.json_path)

    def on_show(self) -> None:
        self._refresh_preview()
