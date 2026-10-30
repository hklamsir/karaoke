# -*- coding: utf-8 -*-
"""pages.home — 主頁（計劃書 §5.1–§5.3；版面依據 docs/線框圖/01_主頁.html）。

三層屬性（B2 決定）：基本（影片/語言/輸出）/ 樣式（字體顏色留白）/ 進階（預設收埋）。
右欄：即時樣式預覽（B3）+ 產出清單 + 「開始生成」主 CTA + 分階段進度（B4）。
"""

from __future__ import annotations

import logging
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


class HomePage(ttkb.Frame):
    """主頁：新任務 / 開啟現有 / 三層屬性面板 / 樣式預覽 / 分階段進度。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        self._video_path: Path | None = None
        self._video_info: dict[str, Any] = {}
        self._running = False

        left = ttkb.Frame(self, bootstyle="dark")
        left.pack(side="left", fill="both", expand=True, padx=(0, theme.SPACE))
        right = ttkb.Frame(self, bootstyle="dark")
        right.pack(side="left", fill="y", padx=(0, theme.SPACE))

        self._build_basic(left)
        self._build_style(left)
        self._build_advanced(left)
        self._build_right(right)

    # ------------------------------------------------------------------
    # 左欄 · 基本
    # ------------------------------------------------------------------

    def _build_basic(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="基本", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))

        row = ttkb.Frame(box, bootstyle="dark")
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

        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=2)
        ttkb.Label(row, text="語言提示", width=10, bootstyle="secondary").pack(side="left")
        self._lang_var = tk.StringVar(value="zh")
        # ⚠️ ADR-001：yue 係「粵語→普通話」開關，UI 直接畀 zh（內部照樣重映射防呆）
        ttkb.Radiobutton(row, text="粵語 / 中文", variable=self._lang_var, value="zh") \
            .pack(side="left", padx=(0, theme.SPACE))
        ttkb.Radiobutton(row, text="英文", variable=self._lang_var, value="en") \
            .pack(side="left")

        row = ttkb.Frame(box, bootstyle="dark")
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
        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=(0, theme.SPACE))
        ttkb.Button(row, text="📂 開啟現有 *_timings.json…", bootstyle="secondary",
                    takefocus=False, command=self._open_existing).pack(side="left")

    # ------------------------------------------------------------------
    # 左欄 · 樣式
    # ------------------------------------------------------------------

    def _build_style(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="樣式", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))

        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=(theme.SPACE, 2))
        ttkb.Label(row, text="字體", width=10, bootstyle="secondary").pack(side="left")
        families = self._font_families()
        self._font_var = tk.StringVar(value="Microsoft JhengHei")
        self._font_combo = ttkb.Combobox(row, textvariable=self._font_var,
                                         values=families, width=26)
        self._font_combo.pack(side="left", padx=(0, theme.SPACE))
        self._font_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        self._font_combo.bind("<KeyRelease>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="字級", bootstyle="secondary").pack(side="left")
        self._size_var = tk.IntVar(value=48)
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
        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=2)
        for key, label, default in colors:
            cell = ttkb.Frame(row, bootstyle="dark")
            cell.pack(side="left", padx=(0, theme.SPACE))
            ttkb.Label(cell, text=label, bootstyle="secondary").pack(anchor="w")
            line = ttkb.Frame(cell, bootstyle="dark")
            line.pack()
            swatch = tk.Canvas(line, width=30, height=22, highlightthickness=1,
                               highlightbackground=theme.BORDER, bg=default, cursor="hand2")
            swatch.pack(side="left")
            swatch.bind("<Button-1>", lambda _e, k=key: self._pick_color(k))
            self._swatches[key] = swatch
            var = tk.StringVar(value=default)
            var.trace_add("write", lambda *_a, k=key: self._on_hex_changed(k))
            entry = ttkb.Entry(line, textvariable=var, width=9)
            entry.pack(side="left", padx=(4, 0))
            self._color_vars[key] = var

        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=2)
        ttkb.Label(row, text="描邊粗幼", width=10, bootstyle="secondary").pack(side="left")
        self._outline_var = tk.StringVar(value=_OUTLINE_WIDTHS[0][0])
        combo = ttkb.Combobox(row, textvariable=self._outline_var,
                              values=[n for n, _ in _OUTLINE_WIDTHS], width=8,
                              state="readonly")
        combo.pack(side="left", padx=(0, theme.SPACE))
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())
        ttkb.Label(row, text="對齊", bootstyle="secondary").pack(side="left")
        self._align_var = tk.StringVar(value=_ALIGNMENTS[0][0])
        combo = ttkb.Combobox(row, textvariable=self._align_var,
                              values=[n for n, _ in _ALIGNMENTS], width=10,
                              state="readonly")
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_preview())

        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=(2, theme.SPACE))
        ttkb.Label(row, text="底部留白", width=10, bootstyle="secondary").pack(side="left")
        self._margin_var = tk.IntVar(value=0)
        scale = ttkb.Scale(row, from_=0, to=400, variable=self._margin_var,
                           command=lambda _v: self._refresh_preview(), length=200)
        scale.pack(side="left", padx=4)
        self._margin_label = ttkb.Label(row, text="0 = 自動（1080p ≈ 59px）",
                                        bootstyle="secondary",
                                        font=(theme.mono_font_family(), 9))
        self._margin_label.pack(side="left", padx=theme.SPACE)

    # ------------------------------------------------------------------
    # 左欄 · 進階（預設收埋，B2）
    # ------------------------------------------------------------------

    def _build_advanced(self, parent) -> None:
        self._adv_open = False
        self._adv_btn = ttkb.Button(parent, text="▸ 進階", bootstyle="secondary",
                                    takefocus=False, command=self._toggle_advanced)
        self._adv_btn.pack(anchor="w", pady=(0, 2))

        self._adv_box = ttkb.LabelFrame(parent, text="進階", bootstyle="dark")
        # 唔 pack，摺疊狀態

        grid = ttkb.Frame(self._adv_box, bootstyle="dark")
        grid.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)

        self._keep_punct_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(grid, text="保留標點（--keep-punctuation）",
                         variable=self._keep_punct_var, bootstyle="dark-round-toggle") \
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
                         variable=self._separate_var, bootstyle="dark-round-toggle") \
            .grid(row=4, column=0, columnspan=2, sticky="w")
        self._save_raw_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(grid, text="儲存 ASR 原始 JSON", variable=self._save_raw_var,
                         bootstyle="dark-round-toggle").grid(row=4, column=3, columnspan=2, sticky="w")

        row = ttkb.Frame(self._adv_box, bootstyle="dark")
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
        box = ttkb.LabelFrame(parent, text="即時預覽", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))
        self._preview = StylePreviewCanvas(box)
        self._preview.pack(padx=theme.SPACE, pady=theme.SPACE)
        legend = ttkb.Frame(box, bootstyle="dark")
        legend.pack(padx=theme.SPACE, pady=(0, theme.SPACE))
        for color, label in ((theme.SUB_PRIMARY, "唱過"), (theme.SUB_SECONDARY, "未唱"),
                             ("#000000", "描邊")):
            tk.Canvas(legend, width=12, height=12, bg=color,
                      highlightthickness=1, highlightbackground=theme.BORDER) \
                .pack(side="left")
            ttkb.Label(legend, text=label, bootstyle="secondary").pack(side="left", padx=(2, theme.SPACE))
        ttkb.Label(box, text="預覽畫布 344×180 · 系統字體渲染，唔係 libass 逐像素預覽",
                   bootstyle="secondary",
                   font=(theme.ui_font_family(), 8)).pack(padx=theme.SPACE, pady=(0, theme.SPACE))

        box = ttkb.LabelFrame(parent, text="產出", bootstyle="dark")
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
        ttkb.Button(parent, text="⧉ 複製等價 CLI 指令", bootstyle="secondary-link",
                    takefocus=False, command=self._copy_cli).pack(fill="x", pady=(0, theme.SPACE))

        box = ttkb.LabelFrame(parent, text="進度", bootstyle="dark")
        box.pack(fill="x", pady=(0, 0))
        self._progress = StageProgress(box)
        self._progress.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------

    def _font_families(self) -> list[str]:
        from ..widgets.style_preview import tk_font_families_safe
        families = tk_font_families_safe()
        return families or ["Microsoft JhengHei"]

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

    def _on_hex_changed(self, key: str) -> None:
        value = self._color_vars[key].get().strip()
        if len(value) == 6:
            value = "#" + value
        try:
            if len(value) == 7:
                int(value[1:], 16)
                self._swatches[key].itemconfigure("all", fill=value) if False else None
                self._swatches[key]["bg"] = value
        except ValueError:
            pass
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
            margin_v=margin)

    def _outline_width_value(self) -> float:
        name = self._outline_var.get()
        return dict(_OUTLINE_WIDTHS).get(name, 0.0)

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
            style = main.StyleConfig(
                font_name=self._font_var.get().strip() or "Microsoft JhengHei",
                font_size=max(8, int(self._size_var.get())),
                primary_color=self._color_vars["primary"].get().strip(),
                secondary_color=self._color_vars["secondary"].get().strip(),
                outline_color=self._color_vars["outline"].get().strip(),
                margin_v=int(self._margin_var.get()),
            )
        except (ValueError, tk.TclError):
            messagebox.showerror("樣式參數錯誤", "字級 / 留白要係整數。", parent=self)
            return None
        return main.GenerateOptions(
            input=video,
            output_dir=Path(self._output_var.get().strip()) if self._output_var.get().strip() else None,
            model=self._model_var.get().strip(),
            language=self._lang_var.get(),
            separate_vocals=self._separate_var.get(),
            max_chars=int(self._adv_spins["max_chars"].get()),
            gap_threshold_ms=int(self._adv_spins["gap_threshold_ms"].get()),
            hold_ms=int(self._adv_spins["hold_ms"].get()),
            min_clause_chars=int(self._adv_spins["min_clause_chars"].get()),
            min_clause_ms=int(self._adv_spins["min_clause_ms"].get()),
            merge_max_chars=int(self._adv_spins["merge_max_chars"].get()),
            keep_punctuation=self._keep_punct_var.get(),
            save_raw=self._save_raw_var.get(),
            style=style,
        )

    def _start_generate(self) -> None:
        if self._running:
            messagebox.showinfo("進行中", "已經有任務跑緊。", parent=self)
            return
        opts = self._build_options()
        if opts is None:
            return
        self._running = True
        self._generate_btn.configure(state="disabled")
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
                result = main.generate_timings(opts, progress_cb=self._progress.report)
            except SystemExit as exc:      # main 用 sys.exit 報錯（保留 CLI exit code）
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
                self.after(0, lambda: self._generate_btn.configure(state="normal"))
                self.after(0, lambda: setattr(self, "_running", False))

        threading.Thread(target=worker, daemon=True).start()

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
