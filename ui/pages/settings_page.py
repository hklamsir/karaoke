# -*- coding: utf-8 -*-
"""pages.settings_page — 設定頁（計劃書 §5.4；版面依據 docs/線框圖/03_設定頁.html）。

API key 優先序（同 main.resolve_api_key 一致）：--api-key > 環境變數 > .env > settings.json。
UI 寫入 settings.json 嘅值**唔會**蓋過環境變數。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from pathlib import Path

import ttkbootstrap as ttkb

from .. import settings, theme

_THEMES = ["darkly", "superhero", "litera"]


class SettingsPage(ttkb.Frame):
    """設定：API key / 工具路徑 / 預設資料夾 / 外觀 / 快取。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        outer = ttkb.Frame(self, bootstyle="dark")
        outer.pack(fill="both", expand=True)
        outer.pack_propagate(False)

        # 內容固定寬度欄（940px）＋ scroll 留白
        col = ttkb.Frame(outer, bootstyle="dark")
        col.pack(anchor="n", fill="y", padx=theme.SPACE, pady=theme.SPACE)

        self._build_api_key(col)
        self._build_tools(col)
        self._build_defaults(col)
        self._build_appearance(col)
        self._build_cache(col)

    # ------------------------------------------------------------------

    def _build_api_key(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="DashScope API Key", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))

        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)
        self._key_var = tk.StringVar()
        self._key_entry = ttkb.Entry(row, textvariable=self._key_var, width=48, show="•")
        self._key_entry.pack(side="left")
        self._show_var = tk.BooleanVar(value=False)
        ttkb.Checkbutton(row, text="顯示", variable=self._show_var,
                         bootstyle="dark-round-toggle",
                         command=lambda: self._key_entry.configure(
                             show="" if self._show_var.get() else "•")).pack(side="left", padx=theme.SPACE)
        ttkb.Button(row, text="測試連線", bootstyle="secondary", takefocus=False,
                    command=self._test_connection).pack(side="left")

        self._key_source_var = tk.StringVar(value="")
        ttkb.Label(box, textvariable=self._key_source_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 9)).pack(anchor="w", padx=theme.SPACE, pady=(0, theme.SPACE))

    def _build_tools(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="工具路徑", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))
        self._ffmpeg_var = tk.StringVar()
        self._ffprobe_var = tk.StringVar()
        for label, var in (("FFmpeg", self._ffmpeg_var), ("FFprobe", self._ffprobe_var)):
            row = ttkb.Frame(box, bootstyle="dark")
            row.pack(fill="x", padx=theme.SPACE, pady=2)
            ttkb.Label(row, text=label, width=8, bootstyle="secondary").pack(side="left")
            ttkb.Entry(row, textvariable=var, width=60).pack(side="left", fill="x", expand=True, padx=theme.SPACE)
            ttkb.Button(row, text="選擇…", bootstyle="secondary", takefocus=False,
                        command=lambda v=var: self._pick_executable(v)).pack(side="left")
        self._tool_hint_var = tk.StringVar(value="")
        ttkb.Label(box, textvariable=self._tool_hint_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 9)).pack(anchor="w", padx=theme.SPACE, pady=(0, theme.SPACE))

    def _build_defaults(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="預設資料夾", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))
        self._input_dir_var = tk.StringVar()
        self._output_dir_var = tk.StringVar()
        for label, var in (("輸入", self._input_dir_var), ("輸出", self._output_dir_var)):
            row = ttkb.Frame(box, bootstyle="dark")
            row.pack(fill="x", padx=theme.SPACE, pady=2)
            ttkb.Label(row, text=label, width=8, bootstyle="secondary").pack(side="left")
            ttkb.Entry(row, textvariable=var, width=60).pack(side="left", fill="x", expand=True, padx=theme.SPACE)
            ttkb.Button(row, text="選擇…", bootstyle="secondary", takefocus=False,
                        command=lambda v=var: self._pick_dir(v)).pack(side="left")

    def _build_appearance(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="外觀", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))
        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)
        ttkb.Label(row, text="主題", width=8, bootstyle="secondary").pack(side="left")
        self._theme_var = tk.StringVar(value="darkly")
        combo = ttkb.Combobox(row, textvariable=self._theme_var, values=_THEMES,
                              width=12, state="readonly")
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_theme())

    def _build_cache(self, parent) -> None:
        box = ttkb.LabelFrame(parent, text="縮圖快取", bootstyle="dark")
        box.pack(fill="x", pady=(0, theme.SPACE))
        row = ttkb.Frame(box, bootstyle="dark")
        row.pack(fill="x", padx=theme.SPACE, pady=theme.SPACE)
        self._cache_var = tk.StringVar()
        ttkb.Label(row, textvariable=self._cache_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 9)).pack(side="left", padx=(0, theme.SPACE))
        ttkb.Button(row, text="清除縮圖快取", bootstyle="danger-outline", takefocus=False,
                    command=self._clear_cache).pack(side="left")

    # ------------------------------------------------------------------

    def _pick_executable(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(title="揀執行檔",
                                          filetypes=[("執行檔", "*.exe"), ("全部檔案", "*.*")])
        if path:
            var.set(path)

    def _pick_dir(self, var: tk.StringVar) -> None:
        path = filedialog.askdirectory(title="揀資料夾")
        if path:
            var.set(path)

    def _apply_theme(self) -> None:
        import ttkbootstrap as ttkb
        name = self._theme_var.get()
        try:
            ttkb.Style().theme_use(name)
            self.master.save_settings({**self.master.settings_data, "theme": name})
        except Exception as exc:   # noqa: BLE001
            messagebox.showerror("主題切換失敗", str(exc), parent=self)

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
        total = sum(f.stat().st_size for f in cache.rglob("*") if f.is_file()) if cache.is_dir() else 0
        self._cache_var.set(f"佔用 {total / 1_048_576:.1f} MB　{cache}")

    def _save(self) -> None:
        data = {**self.master.settings_data,
                "dashscope_api_key": self._key_var.get().strip(),
                "ffmpeg_path": self._ffmpeg_var.get().strip(),
                "ffprobe_path": self._ffprobe_var.get().strip(),
                "default_input_dir": self._input_dir_var.get().strip(),
                "default_output_dir": self._output_dir_var.get().strip(),
                "theme": self._theme_var.get()}
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
        self._theme_var.set(data.get("theme", "darkly"))

        key, source = settings.resolve_api_key(data)
        self._key_source_var.set(
            f"生效中 key 來源：{source}" + ("（settings.json 唔會蓋過環境變數）" if "環境" in source else ""))
        ffmpeg = settings.detect_ffmpeg(data)
        ffprobe = settings.detect_ffprobe(data)
        if not ffmpeg:
            self._tool_hint_var.set("⚠ 搵唔到 ffmpeg —— winget install Gyan.FFmpeg 或喺上面填路徑")
        elif not ffprobe:
            self._tool_hint_var.set("⚠ 搵唔到 ffprobe（通常同 ffmpeg 同目錄）")
        else:
            self._tool_hint_var.set(f"● ffmpeg ✓　● ffprobe ✓")
        self._refresh_cache_size()

        # 離開頁面時儲存：bind 用唔到，改為即時儲存（每次改動存一次太密，
        # 折衷：on_hide 統一存）
    def on_hide(self) -> None:
        self._save()
