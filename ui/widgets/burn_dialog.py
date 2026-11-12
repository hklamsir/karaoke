# -*- coding: utf-8 -*-
"""burn_dialog — 燒錄設定：揀「燒錄來源」（ADR-020）+ 音檔背景（ADR-039）。

背景：曾經試過內建 AI 去人聲（audio-separator），但實測**細節流失明顯**，
而且依賴 1.3 GB、慢 18 倍，所以否決（見 ADR-020）。改為：
用戶自己準備好去人聲的「純音樂 MV」，UI 只負責揀檔 + 燒字幕。

ADR-039：用戶只有 mp3/wav 時，冇畫面可以燒字幕 → 呢個對話框加「背景」
（純色 / 自選圖片，填滿或完整），由 ffmpeg 合成一條畫面出 mp4。

⚠️ 最大風險係**版本唔同**：自備 MV 若果係另一個剪輯（前奏長短唔同、cut 過），
字幕會整體漂移。呢個對話框用「時長／畫面比例」比對做燒錄前預警，
但唔會（亦唔可能）自動對齊 —— 要對位就返編輯器調時間軸。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.colorchooser as colorchooser
import tkinter.filedialog as filedialog
from pathlib import Path
from typing import Any

import ttkbootstrap as ttkb

from .. import theme
from .dialogs import center_window

#: 對話框寬度（高度按內容自動量）
SIZE = (600, 420)

#: 時長相差超過呢個值就出警告（ms）。1500ms 大約等於「一句歌詞」，
#: 超過就一定會察覺對唔上，細過就當係編碼尾巴差異。
DURATION_WARN_MS = 1500

#: 燒錄來源的檔案類型。影片與音檔放**同一個 filter**，否則開檔對話框
#: 預設只列第一個（影片），用戶要自己切換才見到 mp3／wav。
_VIDEO_FILETYPES = [
    ("影片及音檔", "*.mp4 *.mov *.mkv *.m4v *.webm *.avi *.ts "
                   "*.mp3 *.wav *.m4a *.flac *.aac *.ogg"),
    ("所有檔案", "*.*"),
]

#: 背景圖片可接受的格式
_IMAGE_FILETYPES = [
    ("圖片", "*.jpg *.jpeg *.png *.bmp *.webp"),
    ("所有檔案", "*.*"),
]

#: 背景填充方式（UI 中文 ↔ ffmpeg 參數）
_FIT_LABELS = {"cover": "填滿（裁切）", "contain": "完整（加邊）"}
_FIT_VALUES = {v: k for k, v in _FIT_LABELS.items()}


def _fmt_ms(ms: int | None) -> str:
    if not ms:
        return "—"
    minutes, rest = divmod(int(ms), 60_000)
    seconds, msec = divmod(rest, 1000)
    return f"{minutes}:{seconds:02d}.{msec:03d}"


def _aspect(width: int, height: int) -> float:
    return width / height if height else 0.0


def compare_sources(ffprobe: str, source: Path, original: Path) -> tuple[list[str], bool]:
    """比對「燒錄音源」同原片 → ``(警告清單, 是否可用)``。

    抽出嚟做純函數係為咗可以無 UI 直接測（probe 唔使開窗）。

    Args:
        ffprobe: ffprobe 路徑；空字串 = 跳過比對（唔阻塞燒錄）。
        source: 用戶揀的燒錄音源。
        original: timings.json 記錄的原片（字幕時間軸的基準）。

    Returns:
        ``(warnings, usable)``；``usable=False`` 代表唔可以燒（例如冇視訊軌）。
    """
    import main as _main

    if not ffprobe:
        return [], True

    warnings: list[str] = []
    dur_src = _main.probe_duration_ms(ffprobe, source)
    dur_org = _main.probe_duration_ms(ffprobe, original)
    if dur_src and dur_org and abs(dur_src - dur_org) > DURATION_WARN_MS:
        diff = (dur_src - dur_org) / 1000
        warnings.append(
            f"時長同原片相差 {diff:+.1f} 秒（{_fmt_ms(dur_src)} vs {_fmt_ms(dur_org)}）。"
            "如果唔係同一個剪輯版本，字幕會愈唱愈唔對位。")

    # ADR-039：純音檔唔再落閘，改為提示「會用背景合成」
    if not _main.probe_has_video(ffprobe, source):
        warnings.insert(0, "呢個檔案冇視訊軌（音檔）→ 會用下面揀的背景合成畫面。")
        return warnings, True       # 音檔唔比畫面比例（無從比較，避免噪音警告）

    try:
        sw, sh = _main.probe_video_size(ffprobe, source)
        ow, oh = _main.probe_video_size(ffprobe, original)
        if abs(_aspect(sw, sh) - _aspect(ow, oh)) > 0.05 * max(_aspect(ow, oh), 0.1):
            warnings.append(
                f"畫面比例同原片唔同（{sw}×{sh} vs {ow}×{oh}），字幕位置可能要重新調整。")
    except (TypeError, ValueError):
        pass

    return warnings, True


def ask_burn_source(
    parent: tk.Misc,
    *,
    default_video: Path,
    style_text: str,
    ffprobe: str = "",
    last_dir: Path | None = None,
) -> dict[str, Any] | None:
    """彈出燒錄設定（modal），回傳燒錄計劃。

    Args:
        parent: 父視窗（transient + 置中）。
        default_video: 預設音源 = 原片。
        style_text: 字幕樣式摘要。
        ffprobe: 用嚟做時長／比例比對；空字串 = 唔比對。
        last_dir: 開檔對話框的起始目錄。

    Returns:
        ``{"source": Path, "bg_mode": "color"|"image", "bg_color": "#RRGGBB",
        "bg_image": str|None, "bg_fit": "cover"|"contain", "has_video": bool, ...}``；
        取消 → ``None``。

        ADR-039 起改為回傳 dict（以前只回 Path）—— 燒錄需要「背景」資訊，
        而背景只對純音檔有意義；``has_video=True`` 時呼叫方應忽略背景欄位。
    """
    win, state, _on_ok = build_burn_dialog(
        parent, default_video=default_video, style_text=style_text,
        ffprobe=ffprobe, last_dir=last_dir)
    _grab_when_ready(win)
    win.wait_window()
    # 確認路徑會 destroy 個窗，所以靠旗標分辨「確認」同「取消／關窗」
    return dict(state) if state.get("confirmed") else None


def _grab_when_ready(win: ttkb.Toplevel, retries: int = 15) -> None:
    """嘗試擷取輸入焦點；未 map 就隔 80ms 再試（X11 未 viewable 會拋 TclError）。"""
    try:
        if not win.winfo_exists():
            return
        win.grab_set()
    except tk.TclError:
        if retries > 0:
            win.after(80, lambda: _grab_when_ready(win, retries - 1))


def build_burn_dialog(
    parent: tk.Misc,
    *,
    default_video: Path,
    style_text: str,
    ffprobe: str = "",
    last_dir: Path | None = None,
) -> tuple[ttkb.Toplevel, dict, callable]:
    """建立（但唔阻塞）燒錄設定視窗 —— 供截圖／自動化測試。

    Returns:
        ``(視窗, state, on_ok)``。``state`` 有 ``source`` / ``confirmed`` / ``usable`` /
        ``warnings``；``on_ok()`` 會寫 ``confirmed=True`` 再 destroy。
    """
    state: dict = {"source": default_video, "confirmed": False, "usable": True,
                   "warnings": [], "has_video": True,
                   # ADR-039：純音檔的背景設定
                   "bg_mode": "color", "bg_color": "#000000",
                   "bg_image": None, "bg_fit": "cover"}

    win = ttkb.Toplevel(title="燒錄設定", size=SIZE)
    win.transient(parent.winfo_toplevel())
    win.resizable(False, False)

    # ⚠️ 建立次序 ≠ pack 次序。Tk 的 pack 在空間不足時會裁掉**最後 pack** 的 widget，
    #    所以 footer 按鈕一定要**先 pack**（side="bottom"），內容才 fill 剩餘空間；
    #    否則視窗高度量少幾 px 就會連「開始燒錄」都見唔到（實測踩過）。
    body = ttkb.Frame(win, bootstyle="light", padding=(20, 16, 20, 8))
    btns = ttkb.Frame(win, bootstyle="light", padding=(20, 0, 20, 16))

    def on_ok() -> None:
        if not state.get("usable", True):
            return                  # 揀錯檔（例如 mp3）唔准落閘
        state["confirmed"] = True
        win.destroy()

    start_btn = ttkb.Button(btns, text="開始燒錄", bootstyle=theme.ACCENT, width=12,
                            takefocus=False, command=lambda: on_ok())
    start_btn.pack(side="right")
    ttkb.Button(btns, text="取消", bootstyle="secondary", width=8,
                takefocus=False, command=win.destroy).pack(side="right", padx=(0, 8))
    btns.pack(side="bottom", fill="x")
    body.pack(fill="both", expand=True)

    # ---- 標題 ----
    ttkb.Label(body, text="燒錄設定", font=(theme.ui_font_family(), 14, "bold"),
               bootstyle="light").pack(anchor="w")
    ttkb.Label(body, text=f"字幕時間軸來自：{default_video.name}", bootstyle="secondary",
               font=(theme.ui_font_family(), 9), wraplength=SIZE[0] - 60,
               justify="left").pack(anchor="w", pady=(6, 10))

    # ---- 音源 ----
    ttkb.Label(body, text="燒錄來源（影片或音檔）",
               font=(theme.ui_font_family(), 10, "bold"),
               bootstyle="light").pack(anchor="w")
    ttkb.Separator(body).pack(fill="x", pady=(2, 6))

    name_var = tk.StringVar()
    ttkb.Label(body, textvariable=name_var, bootstyle="light",
               font=(theme.ui_font_family(), 10, "bold"),
               wraplength=SIZE[0] - 60, justify="left").pack(anchor="w")

    ttkb.Label(body,
               text="預設用原片。可換成你自備的純音樂 MV（畫面與音軌都取自該檔），"
                    "或直接揀 mp3／wav 音檔 —— 音檔會用下方「背景」合成畫面。",
               bootstyle="secondary", font=(theme.ui_font_family(), 9),
               wraplength=SIZE[0] - 60, justify="left").pack(anchor="w", pady=(4, 8))

    pick_row = ttkb.Frame(body, bootstyle="light")
    pick_row.pack(fill="x")
    ttkb.Button(pick_row, text="選擇影片／音檔…", bootstyle=theme.ACCENT, takefocus=False,
                command=lambda: _pick()).pack(side="left")
    ttkb.Button(pick_row, text="還原為原片", bootstyle="secondary", takefocus=False,
                command=lambda: _reset()).pack(side="left", padx=(8, 0))

    info_var = tk.StringVar()
    ttkb.Label(body, textvariable=info_var, bootstyle="secondary",
               font=(theme.ui_font_family(), 9), wraplength=SIZE[0] - 60,
               justify="left").pack(anchor="w", pady=(8, 0))

    warn_label = ttkb.Label(body, text="", bootstyle="warning",
                            font=(theme.ui_font_family(), 9),
                            wraplength=SIZE[0] - 60, justify="left")

    style_label = ttkb.Label(body, text=f"字幕樣式：{style_text}", bootstyle="secondary",
                             font=(theme.ui_font_family(), 9), wraplength=SIZE[0] - 60,
                             justify="left")
    style_label.pack(anchor="w", pady=(10, 0))

    # ---- 背景（ADR-039：只有來源係純音檔才顯示）----
    bg_box = ttkb.LabelFrame(body, text="背景（來源係音檔時使用）", bootstyle="light")
    bg_mode_var = tk.StringVar(value="color")
    bg_color_var = tk.StringVar(value="#000000")
    bg_image_var = tk.StringVar(value="")
    bg_fit_var = tk.StringVar(value=_FIT_LABELS["cover"])

    mode_row = ttkb.Frame(bg_box, bootstyle="light")
    mode_row.pack(fill="x", padx=10, pady=(6, 2))
    ttkb.Radiobutton(mode_row, text="純色", variable=bg_mode_var, value="color",
                     bootstyle="primary",
                     command=lambda: _sync_bg()).pack(side="left")
    ttkb.Radiobutton(mode_row, text="圖片", variable=bg_mode_var, value="image",
                     bootstyle="primary",
                     command=lambda: _sync_bg()).pack(side="left", padx=(10, 0))

    color_row = ttkb.Frame(bg_box, bootstyle="light")
    color_row.pack(fill="x", padx=10, pady=2)
    ttkb.Label(color_row, text="顏色", width=5, bootstyle="secondary").pack(side="left")
    color_swatch = tk.Canvas(color_row, width=28, height=20, highlightthickness=1,
                             highlightbackground=theme.BORDER, bg="#000000",
                             cursor="hand2")
    color_swatch.pack(side="left")
    color_swatch.bind("<Button-1>", lambda _e: _pick_color())
    ttkb.Label(color_row, textvariable=bg_color_var, bootstyle="secondary",
               font=(theme.mono_font_family(), 9)).pack(side="left", padx=(6, 0))

    image_row = ttkb.Frame(bg_box, bootstyle="light")
    image_row.pack(fill="x", padx=10, pady=2)
    ttkb.Label(image_row, text="圖片", width=5, bootstyle="secondary").pack(side="left")
    ttkb.Label(image_row, textvariable=bg_image_var, bootstyle="secondary",
               font=(theme.ui_font_family(), 9), width=30, anchor="w").pack(side="left")
    ttkb.Button(image_row, text="選擇…", bootstyle="secondary", takefocus=False,
                command=lambda: _pick_image()).pack(side="left", padx=(6, 0))

    fit_row = ttkb.Frame(bg_box, bootstyle="light")
    fit_row.pack(fill="x", padx=10, pady=(2, 6))
    ttkb.Label(fit_row, text="填充", width=5, bootstyle="secondary").pack(side="left")
    fit_combo = ttkb.Combobox(fit_row, textvariable=bg_fit_var, state="readonly",
                              values=list(_FIT_LABELS.values()), width=14)
    fit_combo.pack(side="left")
    fit_combo.bind("<<ComboboxSelected>>", lambda _e: _sync_bg())

    def _sync_bg() -> None:
        """把背景控制項寫入 ``state``；圖片模式未揀檔就唔准燒。"""
        state["bg_mode"] = bg_mode_var.get()
        state["bg_color"] = bg_color_var.get() or "#000000"
        state["bg_image"] = bg_image_var.get() or None
        state["bg_fit"] = _FIT_VALUES.get(bg_fit_var.get(), "cover")
        need_image = state["bg_mode"] == "image" and not state["bg_image"]
        state["usable"] = True if state.get("has_video") else not need_image
        start_btn.configure(state="normal" if state["usable"] else "disabled")
        _fit()

    def _pick_color() -> None:
        rgb = colorchooser.askcolor(color=bg_color_var.get(), parent=win)
        if rgb and rgb[1]:
            bg_color_var.set(str(rgb[1]).upper())
            color_swatch.configure(bg=str(rgb[1]))
            _sync_bg()

    def _pick_image() -> None:
        chosen = filedialog.askopenfilename(
            parent=win, title="揀背景圖片",
            initialdir=str(last_dir or default_video.parent),
            filetypes=_IMAGE_FILETYPES)
        if not chosen:
            return
        bg_image_var.set(chosen)
        bg_mode_var.set("image")
        _sync_bg()

    # ---- 動態刷新 ----
    def _fit() -> None:
        """按內容重新貼合視窗高度。

        ⚠️ 唔可以只喺建立時量一次：警告 label 係**動態 pack** 的，
        固定 geometry 就會把最後 pack 的 widget 裁走（實測：警告完全睇唔到）。
        只改高濶、唔改 x/y → 視窗唔會跳位。
        """
        try:
            win.update_idletasks()
            win.geometry(f"{SIZE[0]}x{win.winfo_reqheight()}")
        except tk.TclError:
            pass

    def _refresh() -> None:
        src: Path = state["source"]
        name_var.set(src.name)
        warnings, usable = compare_sources(ffprobe, src, default_video)
        state["warnings"] = warnings
        state["usable"] = usable

        has_video = True
        if ffprobe:
            import main as _main
            has_video = _main.probe_has_video(ffprobe, src)
            info_var.set(
                f"時長 {_fmt_ms(_main.probe_duration_ms(ffprobe, src))}"
                f"　·　原片 {_fmt_ms(_main.probe_duration_ms(ffprobe, default_video))}")
        else:
            info_var.set("")
        state["has_video"] = has_video

        # ADR-039：先 pack 警告、再 pack 背景區 —— 讀者由上往下見到
        # 「冇視訊軌」才見到「背景揀咩」，文案的「下面」才指得正確。
        if warnings:
            warn_label.configure(text="⚠ " + "\n⚠ ".join(warnings))
            warn_label.pack(anchor="w", pady=(6, 0), before=style_label)
        else:
            warn_label.pack_forget()

        if has_video:
            bg_box.pack_forget()
        else:
            bg_box.pack(fill="x", pady=(8, 0), before=style_label)

        # 開始鍵由 _sync_bg 統一決定（要額外考慮「圖片模式未揀檔」）
        state["usable"] = usable
        _sync_bg()

    def _pick() -> None:
        chosen = filedialog.askopenfilename(
            parent=win, title="揀燒錄來源（影片或音檔）",
            initialdir=str(last_dir or default_video.parent),
            filetypes=_VIDEO_FILETYPES)
        if not chosen:
            return
        state["source"] = Path(chosen)
        _refresh()

    def _reset() -> None:
        state["source"] = default_video
        _refresh()

    win.bind("<Escape>", lambda _e: win.destroy())
    win.bind("<Return>", lambda _e: on_ok())
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    _refresh()

    # 首次置中：用實際 layout 後的高度（wraplength 要到真正 layout 才定稿）
    win.update_idletasks()
    win.update()
    center_window(win, parent, (SIZE[0], win.winfo_reqheight()))
    return win, state, on_ok
