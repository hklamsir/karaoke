# -*- coding: utf-8 -*-
"""burn_dialog — 燒錄設定：揀「燒錄音源」（ADR-020）。

背景：曾經試過內建 AI 去人聲（audio-separator），但實測**細節流失明顯**，
而且依賴 1.3 GB、慢 18 倍，所以否決（見 ADR-020）。改為：
用戶自己準備好去人聲的「純音樂 MV」，UI 只負責揀檔 + 燒字幕。

⚠️ 最大風險係**版本唔同**：自備 MV 若果係另一個剪輯（前奏長短唔同、cut 過），
字幕會整體漂移。呢個對話框用「時長／畫面比例」比對做燒錄前預警，
但唔會（亦唔可能）自動對齊 —— 要對位就返編輯器調時間軸。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.filedialog as filedialog
from pathlib import Path

import ttkbootstrap as ttkb

from .. import theme
from .dialogs import center_window

#: 對話框寬度（高度按內容自動量）
SIZE = (600, 420)

#: 時長相差超過呢個值就出警告（ms）。1500ms 大約等於「一句歌詞」，
#: 超過就一定會察覺對唔上，細過就當係編碼尾巴差異。
DURATION_WARN_MS = 1500

_VIDEO_FILETYPES = [
    ("影片", "*.mp4 *.mov *.mkv *.m4v *.webm *.avi *.ts"),
    ("所有檔案", "*.*"),
]


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
    if not _main.probe_has_video(ffprobe, source):
        return ["呢個檔案冇視訊軌（可能係 mp3 / wav）。燒錄需要畫面，請揀有畫面的純音樂 MV。"], False

    warnings: list[str] = []
    dur_src = _main.probe_duration_ms(ffprobe, source)
    dur_org = _main.probe_duration_ms(ffprobe, original)
    if dur_src and dur_org and abs(dur_src - dur_org) > DURATION_WARN_MS:
        diff = (dur_src - dur_org) / 1000
        warnings.append(
            f"時長同原片相差 {diff:+.1f} 秒（{_fmt_ms(dur_src)} vs {_fmt_ms(dur_org)}）。"
            "如果唔係同一個剪輯版本，字幕會愈唱愈唔對位。")

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
) -> Path | None:
    """彈出燒錄設定（modal），回傳要燒錄的影片路徑。

    Args:
        parent: 父視窗（transient + 置中）。
        default_video: 預設音源 = 原片。
        style_text: 字幕樣式摘要。
        ffprobe: 用嚟做時長／比例比對；空字串 = 唔比對。
        last_dir: 開檔對話框的起始目錄。

    Returns:
        選定的影片路徑；取消 → ``None``。
    """
    win, state, _on_ok = build_burn_dialog(
        parent, default_video=default_video, style_text=style_text,
        ffprobe=ffprobe, last_dir=last_dir)
    _grab_when_ready(win)
    win.wait_window()
    # 確認路徑會 destroy 個窗，所以靠旗標分辨「確認」同「取消／關窗」
    return state["source"] if state.get("confirmed") else None


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
                   "warnings": []}

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
    ttkb.Label(body, text="燒錄音源", font=(theme.ui_font_family(), 10, "bold"),
               bootstyle="light").pack(anchor="w")
    ttkb.Separator(body).pack(fill="x", pady=(2, 6))

    name_var = tk.StringVar()
    ttkb.Label(body, textvariable=name_var, bootstyle="light",
               font=(theme.ui_font_family(), 10, "bold"),
               wraplength=SIZE[0] - 60, justify="left").pack(anchor="w")

    ttkb.Label(body,
               text="預設用原片。若你已自行準備去人聲的純音樂 MV，可換成它 —— "
                    "字幕會燒到它上面（畫面與音軌都取自該檔）。",
               bootstyle="secondary", font=(theme.ui_font_family(), 9),
               wraplength=SIZE[0] - 60, justify="left").pack(anchor="w", pady=(4, 8))

    pick_row = ttkb.Frame(body, bootstyle="light")
    pick_row.pack(fill="x")
    ttkb.Button(pick_row, text="選擇純音樂 MV…", bootstyle=theme.ACCENT, takefocus=False,
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

    ttkb.Label(body, text=f"字幕樣式：{style_text}", bootstyle="secondary",
               font=(theme.ui_font_family(), 9), wraplength=SIZE[0] - 60,
               justify="left").pack(anchor="w", pady=(10, 0))

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

        if ffprobe:
            import main as _main
            info_var.set(
                f"時長 {_fmt_ms(_main.probe_duration_ms(ffprobe, src))}"
                f"　·　原片 {_fmt_ms(_main.probe_duration_ms(ffprobe, default_video))}")
        else:
            info_var.set("")

        if warnings:
            warn_label.configure(text="⚠ " + "\n⚠ ".join(warnings))
            warn_label.pack(anchor="w", pady=(6, 0))
        else:
            warn_label.pack_forget()

        start_btn.configure(state="normal" if usable else "disabled")
        _fit()

    def _pick() -> None:
        chosen = filedialog.askopenfilename(
            parent=win, title="揀你準備好的純音樂 MV",
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
