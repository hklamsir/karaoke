# -*- coding: utf-8 -*-
"""pages.settings_page — 設定頁（計劃書 §5.4；版面依據 docs/線框圖/03_設定頁.html）。

API key 優先序（同 main.resolve_api_key 一致）：--api-key > 環境變數 > .env > settings.json。
UI 寫入 settings.json 嘅值**唔會**蓋過環境變數。

版面（2026-11-02 改版）：原本一行行 `pack` + 固定窄欄，喺大螢幕會縮成中間一條，
字又好細。改為 **grid 兩欄（標籤 / 輸入）**：輸入框 ``sticky="ew"`` 拉滿可用闊度，
標籤固定 10 字位對齊，整體加左右留白 48px。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox

import ttkbootstrap as ttkb

from .. import settings, theme

#: 設定頁左右留白（大螢幕唔好貼邊；同時唔會似舊版縮成中間一條）
PAGE_PAD_X = 48


class SettingsPage(ttkb.Frame):
    """設定：API key / 工具路徑 / 預設資料夾 / 外觀 / 快取。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="light")

        outer = ttkb.Frame(self, bootstyle="light")
        outer.pack(fill="both", expand=True, padx=PAGE_PAD_X, pady=(16, 8))

        self._build_header(outer)
        self._build_api_key(outer)
        self._build_tools(outer)
        self._build_defaults(outer)
        self._build_appearance(outer)
        self._build_cache(outer)

    # ------------------------------------------------------------------
    # 版面輔助
    # ------------------------------------------------------------------

    @staticmethod
    def _card(parent, title: str) -> ttkb.Labelframe:
        """建立一張設定卡（LabelFrame），內部 grid 可以橫向伸展。"""
        box = ttkb.Labelframe(parent, text=title, bootstyle="light",
                              padding=(12, 8, 12, 12))
        box.pack(fill="x", pady=(0, 12))
        box.grid_columnconfigure(1, weight=1)
        return box

    @staticmethod
    def _row_label(box, row: int, text: str) -> None:
        ttkb.Label(box, text=text, bootstyle="secondary", anchor="w", width=10,
                   font=(theme.ui_font_family(), 10)) \
            .grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)

    @staticmethod
    def _hint(box, row: int, var: tk.StringVar) -> None:
        ttkb.Label(box, textvariable=var, bootstyle="secondary", anchor="w",
                   font=(theme.mono_font_family(), 9),
                   wraplength=880, justify="left") \
            .grid(row=row, column=1, columnspan=3, sticky="w", pady=(0, 4))

    # ------------------------------------------------------------------

    def _build_header(self, parent) -> None:
        ttkb.Label(parent, text="設定", bootstyle="light",
                   font=(theme.ui_font_family(), 16, "bold")).pack(anchor="w")
        ttkb.Label(parent,
                   text="所有改動會於離開本頁時自動寫入 settings.json。",
                   bootstyle="secondary", font=(theme.ui_font_family(), 9)) \
            .pack(anchor="w", pady=(2, 14))

    def _build_api_key(self, parent) -> None:
        box = self._card(parent, "DashScope API Key")
        self._row_label(box, 0, "API Key")
        self._key_var = tk.StringVar()
        self._key_entry = ttkb.Entry(box, textvariable=self._key_var, show="•")
        self._key_entry.grid(row=0, column=1, sticky="ew", pady=4)
        self._show_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(box, text="顯示", variable=self._show_var,
                         bootstyle="primary-round-toggle",
                         command=lambda: self._key_entry.configure(
                             show="" if self._show_var.get() else "•")) \
            .grid(row=0, column=2, sticky="e", padx=(12, 12))
        ttkb.Button(box, text="測試連線", bootstyle="secondary", takefocus=False,
                    command=self._test_connection) \
            .grid(row=0, column=3, sticky="e")

        self._key_source_var = tk.StringVar(value="")
        self._hint(box, 1, self._key_source_var)

    def _build_tools(self, parent) -> None:
        box = self._card(parent, "工具路徑")
        self._ffmpeg_var = tk.StringVar()
        self._ffprobe_var = tk.StringVar()
        for row, (label, var) in enumerate((("FFmpeg", self._ffmpeg_var),
                                            ("FFprobe", self._ffprobe_var))):
            self._row_label(box, row, label)
            ttkb.Entry(box, textvariable=var).grid(row=row, column=1, sticky="ew",
                                                   pady=4)
            ttkb.Button(box, text="瀏覽…", bootstyle="secondary", takefocus=False,
                        command=lambda v=var: self._pick_executable(v)) \
                .grid(row=row, column=3, sticky="e", padx=(10, 0))
        self._tool_hint_var = tk.StringVar(value="")
        self._hint(box, 2, self._tool_hint_var)

    def _build_defaults(self, parent) -> None:
        box = self._card(parent, "預設資料夾")
        self._input_dir_var = tk.StringVar()
        self._output_dir_var = tk.StringVar()
        for row, (label, var) in enumerate((("輸入資料夾", self._input_dir_var),
                                            ("輸出資料夾", self._output_dir_var))):
            self._row_label(box, row, label)
            ttkb.Entry(box, textvariable=var).grid(row=row, column=1, sticky="ew",
                                                   pady=4)
            ttkb.Button(box, text="瀏覽…", bootstyle="secondary", takefocus=False,
                        command=lambda v=var: self._pick_dir(v)) \
                .grid(row=row, column=3, sticky="e", padx=(10, 0))

    def _build_appearance(self, parent) -> None:
        box = self._card(parent, "外觀")
        self._row_label(box, 0, "主題")
        # 用戶需求：三套主題都要返嚟（之前為咗避免深／淺混搭而收起，結果冇得揀）
        labels = [label for _key, label in theme.THEME_CHOICES]
        self._theme_var = tk.StringVar()
        self._theme_combo = ttkb.Combobox(box, textvariable=self._theme_var,
                                          values=labels, state="readonly",
                                          width=26,
                                          font=(theme.ui_font_family(), 10))
        self._theme_combo.grid(row=0, column=1, sticky="w", pady=4)
        self._theme_combo.bind("<<ComboboxSelected>>", self._on_theme_selected)
        ttkb.Label(box, text="即時生效，並記入 settings.json（下次開窗沿用）",
                   bootstyle="secondary", font=(theme.ui_font_family(), 9),
                   anchor="w") \
            .grid(row=0, column=2, columnspan=2, sticky="w", padx=(12, 0))
        self._theme_hint_var = tk.StringVar(value="")
        ttkb.Label(box, textvariable=self._theme_hint_var, bootstyle="secondary",
                   font=(theme.ui_font_family(), 9), anchor="w") \
            .grid(row=1, column=1, columnspan=3, sticky="w", pady=(2, 0))

    def _on_theme_selected(self, _event=None) -> None:
        """下拉選主題 → 交主視窗即時套用（ttk style + tk 控件一齊重漆）。"""
        label = self._theme_var.get()
        key = next((k for k, text in theme.THEME_CHOICES if text == label), None)
        if key is None:
            return
        applied = self.master.apply_theme(key)
        self._theme_hint_var.set(f"已套用：{applied}")

    def _build_cache(self, parent) -> None:
        box = self._card(parent, "縮圖快取")
        self._row_label(box, 0, "佔用")
        self._cache_var = tk.StringVar()
        ttkb.Label(box, textvariable=self._cache_var, bootstyle="secondary",
                   anchor="w", font=(theme.mono_font_family(), 9)) \
            .grid(row=0, column=1, sticky="w", pady=4)
        ttkb.Button(box, text="清除縮圖快取", bootstyle="danger-outline",
                    takefocus=False, command=self._clear_cache) \
            .grid(row=0, column=3, sticky="e", padx=(10, 0))

    # ------------------------------------------------------------------

    def _pick_executable(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(title="揀執行檔",
                                          filetypes=[("執行檔", "*.exe"),
                                                     ("全部檔案", "*.*")])
        if path:
            var.set(path)

    def _pick_dir(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="揀資料夾")
        if path:
            var.set(path)

    def _test_connection(self) -> None:
        """驗 key：直接打 DashScope REST model list（唔依賴 dashscope SDK 內部 API，
        SDK 1.x/2.x 類名路徑唔同會爆 ImportError）。"""
        key = self._key_var.get().strip()
        if not key:
            messagebox.showwarning("未填 Key", "請先填 DashScope API Key。", parent=self)
            return

        def worker() -> None:
            ok, msg = False, ""
            try:
                import requests
                resp = requests.get(
                    "https://dashscope.aliyuncs.com/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=15)
                ok = resp.status_code == 200
                msg = f"HTTP {resp.status_code}" + (
                    "" if ok else f" {resp.text[:150]}")
            except Exception as exc:   # noqa: BLE001
                msg = str(exc)[:200]
            self.after(0, lambda: messagebox.showinfo(
                "測試連線", "連線成功 ✅" if ok else f"連線失敗：{msg}", parent=self))

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _clear_cache(self) -> None:
        import shutil
        cache = settings.thumbs_cache_dir()
        if cache.is_dir():
            shutil.rmtree(cache, ignore_errors=True)
        self._refresh_cache_size()
        messagebox.showinfo("清除快取", "縮圖快取已清除。", parent=self)

    def _refresh_cache_size(self) -> None:
        cache = settings.thumbs_cache_dir()
        total = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file()) \
            if cache.is_dir() else 0
        self._cache_var.set(f"佔用 {total / 1_048_576:.1f} MB　{cache}")

    def _save(self) -> None:
        data = {**self.master.settings_data,
                "dashscope_api_key": self._key_var.get().strip(),
                "ffmpeg_path": self._ffmpeg_var.get().strip(),
                "ffprobe_path": self._ffprobe_var.get().strip(),
                "default_input_dir": self._input_dir_var.get().strip(),
                "default_output_dir": self._output_dir_var.get().strip()}
        self.master.save_settings(data)

    # ------------------------------------------------------------------

    def on_show(self) -> None:
        """每次切到設定頁：由 settings 重新載入（唔 cache 舊值）。"""
        data = settings.load()
        self._key_var.set(data.get("dashscope_api_key", ""))
        self._ffmpeg_var.set(data.get("ffmpeg_path", ""))
        self._ffprobe_var.set(data.get("ffprobe_path", ""))
        self._input_dir_var.set(data.get("default_input_dir", ""))
        self._output_dir_var.set(data.get("default_output_dir", ""))
        labels = dict(theme.THEME_CHOICES)
        self._theme_var.set(labels.get(str(data.get("theme")), labels["litera"]))
        self._theme_hint_var.set("")

        _key, source = settings.resolve_api_key(data)
        self._key_source_var.set(
            f"生效中的 key 來源：{source}"
            + ("（settings.json 不會蓋過環境變數）" if "環境" in source else ""))
        ffmpeg = settings.detect_ffmpeg(data)
        ffprobe = settings.detect_ffprobe(data)
        if not ffmpeg:
            self._tool_hint_var.set("⚠ 找不到 ffmpeg —— 可執行 "
                                    "winget install Gyan.FFmpeg，或於上方填寫路徑")
        elif not ffprobe:
            self._tool_hint_var.set("⚠ 找不到 ffprobe（通常與 ffmpeg 同目錄）")
        else:
            self._tool_hint_var.set("● ffmpeg ✓　● ffprobe ✓")
        self._refresh_cache_size()

    def on_hide(self) -> None:
        self._save()
