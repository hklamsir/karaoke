# -*- coding: utf-8 -*-
"""preview — 大 preview 窗（計劃書 §6.7；版面依據 02_編輯器.html）。

三部分：16:9 影片畫面（+字幕疊圖）｜當前字幕卡｜2fps scrub strip。
畫面來源三層降級（§6.7.3）：L1 縮圖快取（預設）→ L2 高清單幀 → （L3 串流禁用）。
播放時 = 音訊行真實時間，每 0.5s 換下一張縮圖（§6.7.3 粗略預覽，夠校時間軸用）。
"""

from __future__ import annotations

import logging
import subprocess
import tkinter as tk
from pathlib import Path

import ttkbootstrap as ttkb

from .. import theme
from .overlay import draw_overlay

LOGGER = logging.getLogger("karaoke.ui.preview")

STRIP_H = 40

#: ADR-032：預覽解析度（"auto" = 跟 timings.json 的 PlayRes，即輸出影片的真實解析度）
RES_PRESETS: list[tuple[str, str]] = [
    ("跟原片", "auto"),
    ("1920×1080", "1920x1080"),
    ("1280×720", "1280x720"),
    ("854×480", "854x480"),
    ("自訂…", "custom"),
]
#: 縮放：fit = 填滿窗格；其餘為相對 1:1 的倍率（超出窗格時自動降級並顯示實際比例）
ZOOM_PRESETS: list[tuple[str, str]] = [
    ("適合視窗", "fit"),
    ("50%", "0.5"),
    ("100%", "1.0"),
]

#: value → 下拉標籤。settings.json 存的是 **value**（``"1920x1080"``），
#: 而 ``dict(RES_PRESETS)`` 是 label→value，方向相反會永遠查唔到 ——
#: 之前就係咁，重開 App 之後「解析度」永遠跳回「跟原片」。
_RES_BY_VALUE = {value: label for label, value in RES_PRESETS}
_ZOOM_BY_VALUE = {value: label for label, value in ZOOM_PRESETS}


def fit_frame(w: float, h: float, base_w: int, base_h: int,
              res_w: int, res_h: int, zoom: float | None) -> tuple[float, float, float]:
    """按**原片比例**把畫面塞入窗格，回傳 ``(fw, fh, scale)``。

    Args:
        w, h: 窗格可用尺寸（px）。
        base_w, base_h: 原片／timings 的 PlayRes（決定比例）。
        res_w, res_h: 使用者選的「輸出模擬」解析度；只用嚟封頂放大倍率，
            唔會改變畫面比例。
        zoom: ``None`` = 適合視窗；否則為相對原片 1:1 的倍率。

    Returns:
        ``(畫面闊, 畫面高, 縮放比例)``。

    Examples:
        4:3 原片（640×480）在 16:9 窗格 + 1920×1080 輸出模擬 → 仍然 4:3，
        唔會像以前咁被拉闊裁走上下。
    """
    base_w = max(1, int(base_w))
    base_h = max(1, int(base_h))
    fit = min(w / base_w, h / base_h)
    want = fit if zoom is None else float(zoom)
    cap = min(res_w / base_w, res_h / base_h) if res_w and res_h else 1.0
    scale = max(0.01, min(want, fit, max(0.01, cap)))
    return base_w * scale, base_h * scale, scale


class PreviewPane(ttkb.Frame):
    """大 preview 窗：影片畫面 + 當前字幕卡 + scrub strip。"""

    def __init__(self, master, state, *,
                 on_seek=None, on_reshow=None, on_video_changed=None) -> None:
        super().__init__(master, bootstyle="light")
        self._state = state
        self._on_seek = on_seek
        self._on_video_changed = on_video_changed
        self._thumbs = None
        # §2.3：正在抽取中的 ThumbCache（同已完成的 _thumbs 分開存）——
        # 換片時要能夠叫停舊的抽取 thread，而 _thumbs 只會在完成後才賦值。
        self._thumbs_task = None
        self._photo: tk.PhotoImage | None = None       # 防 GC（Tk image 唔自動回收）
        #: 原片真實尺寸（畫面比例用；ADR-046）。``None`` = 未 probe，退回 PlayRes。
        self._source_size: tuple[int, int] | None = None
        self._photo_cache: dict[int, tk.PhotoImage] = {}   # frame idx → PhotoImage（LRU 3）
        self._strip_photos: list[tk.PhotoImage] = []
        self._strip_items: list[int] = []
        self._strip_built_for: tuple | None = None     # (video_key, n_expected) 防重複建
        self._overlay_on = True
        self._thumb_progress_var = tk.StringVar(value="")
        self._progress: ttkb.Progressbar | None = None

        body = ttkb.Frame(self, bootstyle="light")
        body.pack(fill="both", expand=True)

        # --- 左：影片畫面（16:9）---
        self._video_canvas = tk.Canvas(body, bg=theme.VIDEO_BG, highlightthickness=1,
                                       highlightbackground=theme.BORDER)
        self._video_canvas.pack(side="left", fill="both", expand=True,
                                padx=(2, theme.SPACE), pady=2)
        self._video_canvas.bind("<Configure>", lambda _e: self._redraw_video())

        # --- 右：當前字幕卡 ---
        card = ttkb.Frame(body, bootstyle="light", width=240)
        card.pack(side="left", fill="y", padx=(0, 2), pady=2)
        card.pack_propagate(False)
        self._card_title = tk.StringVar(value="當前字幕")
        ttkb.Label(card, textvariable=self._card_title, bootstyle="secondary",
                   font=(theme.ui_font_family(), 10)).pack(anchor="w", padx=6, pady=(6, 2))
        self._card_text = tk.StringVar(value="—")
        ttkb.Label(card, textvariable=self._card_text, wraplength=210,
                   font=(theme.ui_font_family(), 13, "bold"),
                   bootstyle="inverse-dark").pack(fill="x", padx=6, pady=2)
        self._card_chars = tk.Canvas(card, height=34, bg=theme.BG_PANEL,
                                     highlightthickness=0)
        self._card_chars.pack(fill="x", padx=6, pady=2)
        self._card_stats = tk.StringVar(value="")
        ttkb.Label(card, textvariable=self._card_stats, bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(anchor="w", padx=6)

        # --- scrub strip ---
        self._strip = tk.Canvas(self, height=STRIP_H, bg=theme.BG_DEEP,
                                highlightthickness=1, highlightbackground=theme.BORDER)
        self._strip.pack(fill="x", padx=2, pady=(2, 2))
        self._strip.bind("<Button-1>", self._on_strip_click)
        self._strip.bind("<B1-Motion>", self._on_strip_click)

        # --- 進度 / 狀態行（含 ADR-032 解析度與縮放）---
        row = ttkb.Frame(self, bootstyle="light")
        row.pack(fill="x", padx=4, pady=(0, 2))
        from .. import settings as ui_settings
        saved = ui_settings.load()
        self._res_var = tk.StringVar(
            value=_RES_BY_VALUE.get(str(saved.get("preview_res") or "auto"),
                                    RES_PRESETS[0][0]))
        self._zoom_var = tk.StringVar(
            value=_ZOOM_BY_VALUE.get(str(saved.get("preview_zoom") or "fit"),
                                     ZOOM_PRESETS[0][0]))
        ttkb.Label(row, text="解析度", bootstyle="secondary",
                   font=(theme.ui_font_family(), 8)).pack(side="left")
        res_combo = ttkb.Combobox(row, textvariable=self._res_var, width=10,
                                  state="readonly", values=[n for n, _ in RES_PRESETS])
        res_combo.pack(side="left", padx=(2, 6))
        res_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_view_setting())
        ttkb.Label(row, text="縮放", bootstyle="secondary",
                   font=(theme.ui_font_family(), 8)).pack(side="left")
        zoom_combo = ttkb.Combobox(row, textvariable=self._zoom_var, width=8,
                                   state="readonly", values=[n for n, _ in ZOOM_PRESETS])
        zoom_combo.pack(side="left", padx=(2, 6))
        zoom_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_view_setting())
        self._view_info_var = tk.StringVar(value="")
        ttkb.Label(row, textvariable=self._view_info_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(side="left", padx=(0, 6))
        ttkb.Label(row, textvariable=self._thumb_progress_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(side="left")
        ttkb.Button(row, text="高清單幀", bootstyle="secondary", takefocus=False,
                    command=self._extract_hd_frame).pack(side="right", padx=2)
        # 2026-11-11：換「最終要燒錄」原片（預覽／波形／燒錄一齊換；timings 不變）
        self._src_video_btn = ttkb.Button(row, text="換原片…", bootstyle="secondary",
                                          takefocus=False, command=self._pick_source_video)
        self._src_video_btn.pack(side="right", padx=2)
        ttkb.Button(row, text="外部播放器", bootstyle="secondary", takefocus=False,
                    command=self._open_external_player).pack(side="right")

        state.add_observer(self._on_state_change)

    # ------------------------------------------------------------------
    # 對外
    # ------------------------------------------------------------------

    def restyle(self) -> None:
        """換主題：重設三個 Canvas 底色並重繪（字幕色係燒錄色，唔跟 UI 主題）。"""
        self._video_canvas.configure(bg=theme.VIDEO_BG,
                                     highlightbackground=theme.BORDER)
        self._strip.configure(bg=theme.BG_DEEP,
                              highlightbackground=theme.BORDER)
        self._card_chars.configure(bg=theme.BG_PANEL)
        self._redraw_video()

    def set_overlay_visible(self, on: bool) -> None:
        self._overlay_on = on
        self._redraw_video()

    def _ui_call(self, fn) -> None:
        """由 worker thread 排 callback 落主線程（冇 mainloop／已關窗就放棄）。"""
        try:
            self.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    def _pick_source_video(self) -> None:
        """換「最終要燒錄」原片（寫入 timings.json 的 ``source.video``）。"""
        from tkinter import filedialog
        current = self._state.video_path
        path = filedialog.askopenfilename(
            title="揀最終要燒錄嘅原片",
            initialdir=str(current.parent) if current is not None else None,
            filetypes=[("影片 / 音檔", "*.mp4 *.mkv *.mov *.webm *.avi "
                                       "*.m4v *.ts *.mp3 *.wav *.m4a *.flac"),
                       ("全部檔案", "*.*")])
        if not path:
            return
        self._state.reassign_video(path)      # 會 notify("edit") → 重繪
        self.reload_source()
        if self._on_video_changed is not None:
            self._on_video_changed()          # 叫 editor 重載音訊／波形

    def _start_thumbs_for(self, video: Path | None) -> None:
        """用指定影片重抽縮圖（換原片用）。"""
        from .. import settings as ui_settings
        if video is None or not Path(video).is_file():
            self.start_thumbs(None, "", "")
            return
        data = self.master.settings_data if hasattr(self.master, "settings_data") else None
        self.start_thumbs(Path(video), ui_settings.detect_ffmpeg(data),
                          ui_settings.detect_ffprobe(data))

    def reload_source(self) -> None:
        """開檔／換片後重載縮圖（editor 的入口）。"""
        self._start_thumbs_for(self._state.video_path)

    # ------------------------------------------------------------------
    # ADR-032：預覽解析度 / 縮放
    # ------------------------------------------------------------------

    def _res_mode(self) -> str:
        return dict(RES_PRESETS).get(self._res_var.get(), "auto")

    def _target_res(self) -> tuple[int, int]:
        """預覽用嘅「影片解析度」；預設跟 timings.json 的 PlayRes（= 輸出真實解析度）。"""
        style = self._state.doc.get("style") or {}
        auto = (int(style.get("play_res_x") or 1920),
                int(style.get("play_res_y") or 1080))
        mode = self._res_mode()
        if mode == "auto":
            return auto
        if mode == "custom":
            from .. import settings as ui_settings
            custom = ui_settings.load().get("preview_res_custom") or []
            try:
                return int(custom[0]), int(custom[1])
            except (IndexError, TypeError, ValueError):
                return auto
        try:
            w_str, h_str = mode.split("x")
            return int(w_str), int(h_str)
        except ValueError:
            return auto

    def set_source_size(self, width: int, height: int) -> None:
        """記錄原片真實尺寸（ADR-046）。

        畫面**比例**用原片真實尺寸，字幕**基準**用 timings 的 PlayRes ——
        兩者可以不同（例如原片 1080p、把 PlayRes 設成 720p 模擬低清輸出）。
        以前畫面比例借用 PlayRes，改解析度就會連比例一齊變，所以分開。
        """
        if width <= 0 or height <= 0:
            return
        size = (int(width), int(height))
        if size == self._source_size:
            return
        self._source_size = size
        self._redraw_video()

    def _frame_geometry(self, w: float, h: float) -> tuple[float, float, float, float, float]:
        """回傳 ``(x0, y0, fw, fh, scale)``。

        ``scale`` 係「畫面像素 ÷ PlayRes 像素」的實際比例 —— 100% 就係 1:1。
        窗格唔夠大時自動降到「適合視窗」並喺狀態列顯示**實際**百分比，
        唔會靜靜畫到爆框。

        ⚠️ 畫面**比例**用原片真實尺寸（ADR-046），字幕**基準**用 timings 的
        PlayRes —— 兩者刻意分開。燒錄只會把字幕疊在原片上，畫面比例永遠跟原片；
        而「解析度」是模擬的輸出解析度，會改變字幕基準（字級 px 不變）。
        以前畫面比例借用 PlayRes，改解析度就會連比例一齊變，4:3 的 MV 揀
        1920×1080 便被拉闊／裁邊（2026-11-12 用戶回報）。
        """
        res_w, res_h = self._target_res()
        style = self._state.doc.get("style") or {}
        if self._source_size:
            base_w, base_h = self._source_size
        else:
            base_w = int(style.get("play_res_x") or res_w) or res_w
            base_h = int(style.get("play_res_y") or res_h) or res_h
        zoom = dict(ZOOM_PRESETS).get(self._zoom_var.get(), "fit")
        fw, fh, scale = fit_frame(w, h, base_w, base_h, res_w, res_h,
                                  None if zoom == "fit" else float(zoom))
        return (w - fw) / 2, (h - fh) / 2, fw, fh, scale

    def _info_text(self, scale: float) -> str:
        """狀態列：原片尺寸 · 顯示比例（· 字幕基準）· 字級警告。

        字級警告用「最長一行字數 × 字級 > PlayRes 闊度」判斷 —— 這正是使用者
        見到「字幕爆框、預覽唔似成品」的原因：字級是相對 PlayRes 的。
        """
        style = self._state.doc.get("style") or {}
        res_w, res_h = self._target_res()
        if self._source_size:
            base_w, base_h = self._source_size
        else:
            base_w = int(style.get("play_res_x") or res_w) or res_w
            base_h = int(style.get("play_res_y") or res_h) or res_h
        play_w = int(style.get("play_res_x") or base_w) or base_w
        play_h = int(style.get("play_res_y") or base_h) or base_h
        text = f"{base_w}×{base_h} · {scale * 100:.0f}%"
        if (play_w, play_h) != (base_w, base_h):
            text += f" · 字幕 {play_w}×{play_h}"
        font = int(style.get("font_size") or 48)
        longest = max((len(str(ln.get("text") or "")) for ln in self._state.lines),
                      default=0)
        if longest and font * longest > play_w:
            text += f" · ⚠ {longest}×{font}>{play_w}"
        return text

    def _on_view_setting(self) -> None:
        """解析度／縮放變更 → 記入 settings.json、對齊字幕基準、重繪。"""
        from .. import settings as ui_settings
        mode = self._res_mode()
        if mode == "custom" and not self._ask_custom_res():
            self._res_var.set("跟原片")      # 取消 → 還原，唔好留住半套設定
            mode = "auto"
        data = ui_settings.load()
        data["preview_res"] = mode
        data["preview_zoom"] = dict(ZOOM_PRESETS).get(self._zoom_var.get(), "fit")
        ui_settings.save(data)
        self._sync_play_res(mode)
        self._photo_cache.clear()      # 舊尺寸的縮圖快取唔可以重用
        self._redraw_video()

    def _sync_play_res(self, mode: str) -> None:
        """把 timings 的 PlayRes 對齊「解析度」所選值（字級 px 不變，ADR-046）。

        Args:
            mode: ``_res_mode()`` 的結果；``"auto"`` = 跟原片真實尺寸。

        為何要改 PlayRes 而唔止改畫面比例：字級與留白都是相對 PlayRes 的 px 值，
        只改畫面比例的話，選 720p 模擬低清輸出時字幕仍然按舊基準縮放，
        見到的字級同所選解析度完全脫節。
        """
        target = (self._source_size if mode == "auto" and self._source_size
                  else self._target_res())
        if not target:
            return
        style = self._state.doc.setdefault("style", {})
        current = (int(style.get("play_res_x") or 0), int(style.get("play_res_y") or 0))
        if current == target:
            return
        style["play_res_x"], style["play_res_y"] = target
        self._state.dirty = True
        self._state.notify("edit")

    def _ask_custom_res(self) -> bool:
        """問自訂闊×高；成功寫入 settings 並回傳 True。"""
        from tkinter import simpledialog
        from .. import settings as ui_settings
        cur = ui_settings.load().get("preview_res_custom") or [1920, 1080]
        answer = simpledialog.askstring(
            "自訂預覽解析度", "闊 × 高（例如 1920x1080）",
            initialvalue=f"{cur[0]}x{cur[1]}", parent=self)
        if not answer:
            return False
        try:
            w_str, h_str = answer.lower().replace("×", "x").split("x")
            width, height = int(w_str.strip()), int(h_str.strip())
        except ValueError:
            return False
        if not (160 <= width <= 7680 and 90 <= height <= 4320):
            return False
        data = ui_settings.load()
        data["preview_res_custom"] = [width, height]
        ui_settings.save(data)
        return True

    def start_thumbs(self, video: Path | None, ffmpeg: str, ffprobe: str) -> None:
        """開檔 / 原片指定後啟動縮圖抽取（背景 thread；§6.7.4）。"""
        # 換片：叫停上一條抽取 thread（§2.3 —— ThumbCache.cancelled 以前
        # 寫好檢查邏輯但全 codebase 冇一處設 True，是死代碼）。
        task = self._thumbs_task
        if task is not None:
            task.cancelled = True
            LOGGER.debug("已叫停上一條縮圖抽取")
        self._thumbs_task = None
        self._thumbs = None
        self._thumb_progress_var.set("")
        if video is None or not video.is_file():
            self._thumb_progress_var.set("原片已移動，無法顯示預覽")
            return
        from ..thumbs import ThumbCache
        cache = ThumbCache(video, ffmpeg, ffprobe)
        self._thumbs_task = cache

        def on_progress(done: int, total: int) -> None:
            self._ui_call(lambda: self._thumb_progress_var.set(
                f"抽縮圖中… {done}/{total}"))

        def on_done(ok: bool) -> None:
            self._ui_call(lambda: self._on_thumbs_ready(cache, ok))

        def worker() -> None:
            ok = cache.prepare(on_progress)
            on_done(ok)

        import threading
        self._thumb_progress_var.set("準備縮圖…")
        threading.Thread(target=worker, daemon=True).start()

    def _on_thumbs_ready(self, cache, ok: bool) -> None:
        if not ok:
            self._thumb_progress_var.set("無法抽取縮圖（其他功能全部照用）")
            return
        self._thumbs = cache
        self._thumb_progress_var.set(
            f"縮圖快取 {cache.count_files()} 張" +
            ("（長片：按需抽）" if cache.on_demand else ""))
        self._build_strip()
        self._redraw_video()

    # ------------------------------------------------------------------
    # 繪製
    # ------------------------------------------------------------------

    def _page_visible(self) -> bool:
        """所屬頁面是否在前景（``App.current_key == "editor"``）。

        播放期間 ``notify("cursor")`` 每 30ms 一次，每次都重畫整個預覽 canvas
        （逐字 8 向描邊 = 每個字 9 個 item）。頁面隱藏時照畫就係「跳去主頁極慢」
        的主因 —— 隱藏時直接跳過，切返嚟由 :meth:`redraw_now` 補一次。
        """
        try:
            key = getattr(self.winfo_toplevel(), "current_key", None)
        except tk.TclError:      # 視窗已銷毀
            return True
        return key in (None, "editor")

    def redraw_now(self) -> None:
        """強制重畫（由 editor ``on_show`` 呼叫，補回隱藏期間漏掉的重繪）。"""
        self._redraw_video()
        self._redraw_strip_playhead()

    def _on_state_change(self, reason: str) -> None:
        if not self._page_visible():
            return
        # ⚠️ strip 重建只准喺 load —— "select"/"edit" 每次都重建會載入成百張
        # PNG 而 freeze UI（實測 542 張）；行編輯唔影響 strip 內容
        if reason == "load":
            self._build_strip()
        self._redraw_video()
        self._redraw_strip_playhead()

    def _current_line(self) -> dict | None:
        """游標所在行（start ≤ cursor < end），冇就最近嘅一行。"""
        lines = self._state.lines
        if not lines:
            return None
        cur = self._state.cursor_ms
        for line in lines:
            if int(line.get("start_ms", 0)) <= cur < int(line.get("end_ms", 0)):
                return line
        return min(lines, key=lambda l: min(
            abs(int(l.get("start_ms", 0)) - cur), abs(int(l.get("end_ms", 0)) - cur)))

    def _draw_overlay(self, canvas, x0: float, y0: float, fw: float, fh: float) -> None:
        """疊圖（單行／雙行／倒數圓點）—— 窗計算同燒錄共用 ``main`` 的純函數。"""
        style = self._state.doc.get("style") or {}
        draw_overlay(canvas, x0, y0, fw, fh, self._state.lines, style,
                     self._state.cursor_ms)

    @staticmethod
    def _fit_photo(photo: tk.PhotoImage, target_w: float) -> tk.PhotoImage:
        """把縮圖縮／放到大約 ``target_w`` 闊（PhotoImage 只支援整數倍）。"""
        if photo.width() <= 0 or target_w <= 0:
            return photo
        ratio = target_w / photo.width()
        if ratio >= 1.5:
            return photo.zoom(max(1, int(round(ratio))))
        if ratio <= 0.66:
            return photo.subsample(max(1, int(round(1 / ratio))))
        return photo

    def _redraw_video(self) -> None:
        canvas = self._video_canvas
        canvas.delete("all")
        w = max(canvas.winfo_width(), 100)
        h = max(canvas.winfo_height(), 60)
        # ADR-032：畫面框按所選解析度／縮放擺位（唔再寫死 16:9）
        x0, y0, fw, fh, scale = self._frame_geometry(w, h)
        self._view_info_var.set(self._info_text(scale))

        self._photo = None
        if self._thumbs is not None:
            idx = max(0, min(self._thumbs.expected - 1,
                             int(self._state.cursor_ms / 1000 * self._thumbs.FPS)))
            # ⚠️ 播放時 notify("cursor") 每 30ms 一次 —— 唔可以每次重新載入 PNG
            # （實測會卡）。cache by frame idx（LRU 3），同一幀直接重用。
            photo = self._photo_cache.get(idx)
            if photo is None:
                path = self._thumbs.frame_path(idx * 1000 // self._thumbs.FPS)
                if path is not None and Path(path).is_file():
                    try:
                        photo = self._fit_photo(tk.PhotoImage(file=str(path)), fw)
                        self._photo_cache[idx] = photo
                        while len(self._photo_cache) > 3:
                            self._photo_cache.pop(next(iter(self._photo_cache)))
                    except Exception as exc:   # noqa: BLE001
                        LOGGER.warning("縮圖載入失敗：%s", exc)
            if photo is not None:
                self._photo = photo
                canvas.create_image(x0, y0, image=photo, anchor="nw")
        else:
            canvas.create_text(x0 + fw / 2, y0 + fh / 2, text="冇預覽畫面",
                               fill=theme.FG_DIM, font=(theme.ui_font_family(), 11))
        canvas.create_rectangle(x0, y0, x0 + fw, y0 + fh, outline=theme.BORDER)

        # 字幕疊圖（§6.7.5）
        if self._overlay_on:
            self._draw_overlay(canvas, x0, y0, fw, fh)

        self._update_card()

    def _update_card(self) -> None:
        line = self._current_line()
        if line is None:
            self._card_title.set("當前字幕")
            self._card_text.set("—")
            self._card_stats.set("")
            self._card_chars.delete("all")
            return
        words = line.get("words") or []
        est = sum(1 for word in words if word.get("estimated"))
        idx = self._state.lines.index(line) + 1
        self._card_title.set(f"當前字幕 · 第 {idx} 行")
        self._card_text.set(line.get("text") or "")
        dur = (int(line.get("end_ms", 0)) - int(line.get("start_ms", 0))) / 1000
        n_chars = sum(len(word.get("chars") or []) for word in words)
        self._card_stats.set(f"字數 {n_chars} · 時長 {dur:.2f}s" +
                             (f" · 估算 {est}" if est else ""))
        # 逐字 chip（估算字橙虛線底）
        canvas = self._card_chars
        canvas.delete("all")
        x = 2
        for word in words:
            for char in word.get("chars") or []:
                sung = self._state.cursor_ms >= int(char.get("begin_ms", 0))
                color = theme.SUB_PRIMARY if sung else theme.FG_MAIN
                outline = theme.WARNING if word.get("estimated") else theme.BORDER
                canvas.create_rectangle(x, 4, x + 24, 30, fill=theme.BG_HOVER,
                                        outline=outline, dash=(2, 2) if word.get("estimated") else ())
                canvas.create_text(x + 12, 17, text=char.get("text", ""),
                                   fill=color, font=(theme.ui_font_family(), 12))
                x += 26

    # ------------------------------------------------------------------
    # scrub strip
    # ------------------------------------------------------------------

    def _build_strip(self, start_i: int = 0) -> None:
        """建 scrub strip。**增量分批**（每批 40 張，after 排程）——同步載幾百張
        PNG 會 freeze UI（實測 542 張卡死）。長片 on-demand 模式只畫空條 + 播放頭。
        """
        strip = self._strip
        if self._thumbs is None:
            return
        key = (str(self._thumbs.video), self._thumbs.expected)
        if start_i == 0:
            if self._strip_built_for == key and self._strip_items:
                return   # 已建過同一條片，唔好重建
            strip.delete("all")
            self._strip_photos.clear()
            self._strip_items.clear()
            self._strip_built_for = key
        w = max(strip.winfo_width(), 100)
        n = self._thumbs.expected
        if self._thumbs.on_demand:
            self._redraw_strip_playhead()
            return
        # 抽稀：strip 每格起碼 ~10px，唔使 1:1 載全部縮圖
        target_thumbs = max(1, w // 10)
        step = max(1, -(-n // target_thumbs))
        limit = n
        batch_end = min(limit, start_i + 40)
        for i in range(start_i, batch_end):
            if i % step:
                continue
            path = self._thumbs.cache_dir / f"{i:05d}.png"
            if not path.is_file():
                continue
            try:
                photo = tk.PhotoImage(file=str(path))
            except Exception:   # noqa: BLE001
                continue
            self._strip_photos.append(photo)
            x = i / n * w
            item = strip.create_image(x, STRIP_H // 2, image=photo, anchor="w")
            self._strip_items.append(item)
            strip.create_rectangle(x, 2, x + max(2, w / n), STRIP_H - 2,
                                   outline=theme.BORDER)
        if batch_end < limit:
            self.after(24, lambda: self._build_strip(batch_end))
        else:
            self._redraw_strip_playhead()

    def _redraw_strip_playhead(self) -> None:
        strip = self._strip
        strip.delete("playhead")
        if self._thumbs is None:
            return
        w = max(strip.winfo_width(), 100)
        total = max(1, self._thumbs.duration_ms)
        x = w * self._state.cursor_ms / total
        strip.create_line(x, 0, x, STRIP_H, fill=theme.ACCENT, width=2, tags=("playhead",))

    def _on_strip_click(self, event) -> None:
        if self._thumbs is None or self._on_seek is None:
            return
        w = max(self._strip.winfo_width(), 100)
        total = max(1, self._thumbs.duration_ms)
        ms = int(event.x / w * total)
        self._state.set_cursor(ms)
        self._on_seek(ms)

    # ------------------------------------------------------------------
    # L2 高清單幀 / 外部播放器（§6.7.8）
    # ------------------------------------------------------------------

    def _extract_hd_frame(self) -> None:
        """L2：抽原分辨率單幀取代縮圖顯示。"""
        if self._thumbs is None:
            return
        import tempfile
        ts = self._state.cursor_ms / 1000
        out = Path(tempfile.gettempdir()) / f"karaoke_hd_{int(ts*1000)}.png"
        proc = subprocess.run(
            [self._thumbs.ffmpeg, "-y", "-v", "error", "-ss", f"{ts:.3f}",
             "-i", str(self._thumbs.video), "-frames:v", "1", "-q:v", "2", str(out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not out.is_file():
            self._thumb_progress_var.set("高清單幀抽取失敗")
            return
        try:
            photo = tk.PhotoImage(file=str(out))
            canvas = self._video_canvas
            w = max(canvas.winfo_width(), 100)
            h = max(canvas.winfo_height(), 60)
            x0, y0, fw, fh, scale = self._frame_geometry(w, h)
            # ADR-032：高清單幀按「實際畫面闊度」縮放（唔再只係 16:9）
            photo = self._fit_photo(photo, fw)
            self._view_info_var.set(self._info_text(scale))
            self._photo = photo
            canvas.delete("all")
            canvas.create_image(x0, y0, image=photo, anchor="nw")
            canvas.create_rectangle(x0, y0, x0 + fw, y0 + fh, outline=theme.BORDER)
            if self._overlay_on:
                self._draw_overlay(canvas, x0, y0, fw, fh)
            self._thumb_progress_var.set(f"高清單幀 @ {ts:.1f}s")
        except Exception as exc:   # noqa: BLE001
            self._thumb_progress_var.set(f"高清單幀顯示失敗：{exc}")

    def _open_external_player(self) -> None:
        """§6.7.8：ffplay 由當前時間開始播（~30 行零依賴對沖）。"""
        video = self._state.video_path
        if video is None or not video.is_file():
            messagebox = __import__("tkinter.messagebox", fromlist=["messagebox"])
            messagebox.showinfo("外部播放器", "冇原片可以播。", parent=self)
            return
        from .. import settings as ui_settings
        ffmpeg = ui_settings.detect_ffmpeg(self.master.settings_data
                                           if hasattr(self.master, "settings_data") else None)
        ffplay = str(Path(ffmpeg).with_name("ffplay.exe" if video.is_absolute() and
                                            __import__("os").name == "nt" else "ffplay")) \
            if ffmpeg else "ffplay"
        try:
            subprocess.Popen([ffplay, "-ss", str(self._state.cursor_ms / 1000),
                              "-autoexit", "-window_title", "Karaoke 預覽", str(video)])
        except OSError as exc:
            LOGGER.warning("ffplay 開唔到：%s", exc)
