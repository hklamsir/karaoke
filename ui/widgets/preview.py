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
from .overlay import draw_dual_subtitle, draw_subtitle

LOGGER = logging.getLogger("karaoke.ui.preview")

STRIP_H = 40


class PreviewPane(ttkb.Frame):
    """大 preview 窗：影片畫面 + 當前字幕卡 + scrub strip。"""

    def __init__(self, master, state, *,
                 on_seek=None, on_reshow=None) -> None:
        super().__init__(master, bootstyle="light")
        self._state = state
        self._on_seek = on_seek
        self._thumbs = None
        # §2.3：正在抽取中的 ThumbCache（同已完成的 _thumbs 分開存）——
        # 換片時要能夠叫停舊的抽取 thread，而 _thumbs 只會在完成後才賦值。
        self._thumbs_task = None
        self._photo: tk.PhotoImage | None = None       # 防 GC（Tk image 唔自動回收）
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

        # --- 進度 / 狀態行 ---
        row = ttkb.Frame(self, bootstyle="light")
        row.pack(fill="x", padx=4, pady=(0, 2))
        ttkb.Label(row, textvariable=self._thumb_progress_var, bootstyle="secondary",
                   font=(theme.mono_font_family(), 8)).pack(side="left")
        ttkb.Button(row, text="高清單幀", bootstyle="secondary", takefocus=False,
                    command=self._extract_hd_frame).pack(side="right", padx=2)
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
            self.after(0, lambda d=done, t=total: self._thumb_progress_var.set(
                f"抽縮圖中… {d}/{t}"))

        def on_done(ok: bool) -> None:
            self.after(0, lambda: self._on_thumbs_ready(cache, ok))

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

    def _on_state_change(self, reason: str) -> None:
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
        """按 style 決定單行／雙行疊圖（ADR-027）。"""
        style = self._state.doc.get("style") or {}
        if style.get("dual_line"):
            draw_dual_subtitle(canvas, x0, y0, fw, fh, self._state.lines, style,
                               self._state.cursor_ms)
        else:
            draw_subtitle(canvas, x0, y0, fw, fh, self._current_line(), style,
                          self._state.cursor_ms)

    def _redraw_video(self) -> None:
        canvas = self._video_canvas
        canvas.delete("all")
        w = max(canvas.winfo_width(), 100)
        h = max(canvas.winfo_height(), 60)
        # 16:9 置中
        fw = min(w, round(h * 16 / 9))
        fh = round(fw * 9 / 16)
        x0, y0 = (w - fw) / 2, (h - fh) / 2

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
                        photo = tk.PhotoImage(file=str(path))
                        z = max(1, int(fw // photo.width()))
                        if z > 1:
                            photo = photo.zoom(z)
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
            # 縮到畫布（subsample 整數倍）
            canvas = self._video_canvas
            w = max(canvas.winfo_width(), 100)
            h = max(canvas.winfo_height(), 60)
            sub = max(1, min(photo.width() // max(1, int(w)),
                             photo.height() // max(1, int(h))))
            if sub > 1:
                photo = photo.subsample(sub)
            self._photo = photo
            canvas.delete("all")
            fw = min(w, round(h * 16 / 9))
            fh = round(fw * 9 / 16)
            x0, y0 = (w - fw) / 2, (h - fh) / 2
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
