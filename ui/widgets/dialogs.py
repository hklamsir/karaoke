# -*- coding: utf-8 -*-
"""dialogs — 共用對話框輔助（置中擺位）。

背景（用戶回報 #1）：``ttkb.Toplevel(size=...)`` 只設尺寸，**唔會置中**，
視窗會黐喺螢幕左上角附近，喺全螢幕主視窗上面睇落去好突兀。
"""

from __future__ import annotations

import tkinter as tk

__all__ = ["center_window"]


def center_window(win: tk.Toplevel, parent: tk.Misc | None = None,
                  size: tuple[int, int] | None = None) -> None:
    """把 ``win`` 置中於 ``parent``（預設父視窗）並套用尺寸。

    Args:
        win: 要擺位嘅 Toplevel。
        parent: 參考視窗；``None`` = ``win.master``。會自動上溯到 toplevel。
        size: ``(寬, 高)``；``None`` = 用視窗目前要求的尺寸。

    Notes:
        - 主視窗係最大化（``state("zoomed")``）時，``winfo_rootx/width`` 已經係
          螢幕實際範圍，所以計算結果會係「畫面正中」而唔係「左上角」。
        - 最後會夾返入螢幕可視範圍，避免多螢幕 / 父視窗貼邊時飛出畫面。
    """
    win.update_idletasks()
    w, h = size if size else (win.winfo_reqwidth(), win.winfo_reqheight())

    ref: tk.Misc | None = parent if parent is not None else win.master
    px = py = 0
    pw, ph = win.winfo_screenwidth(), win.winfo_screenheight()
    if ref is not None:
        try:
            top = ref.winfo_toplevel()
            top.update_idletasks()
            # 最大化視窗有機會仲未完成 layout（width=1）→ 退回螢幕尺寸
            if top.winfo_width() > 1 and top.winfo_height() > 1:
                px, py = top.winfo_rootx(), top.winfo_rooty()
                pw, ph = top.winfo_width(), top.winfo_height()
        except tk.TclError:
            pass

    x = px + (pw - w) // 2
    y = py + (ph - h) // 2
    # 夾入螢幕（以父視窗原點為下限，避免負座標被 WM 亂擺）
    x = max(px, min(x, win.winfo_screenwidth() - w))
    y = max(py, min(y, win.winfo_screenheight() - h))
    win.geometry(f"{w}x{h}+{x}+{y}")

    # ⚠️ geometry 定位嘅係「外框」，而 winfo_rootx/rooty 係「客戶區」；
    #    Windows 標題列 + 邊框會令實際位置偏右下（實測 +8/+31px）→ 量返再補償。
    try:
        win.update_idletasks()
        win.update()
        dx = win.winfo_rootx() - x
        dy = win.winfo_rooty() - y
        if dx or dy:
            win.geometry(f"{w}x{h}+{x - dx}+{y - dy}")
            win.update_idletasks()
    except tk.TclError:
        pass
