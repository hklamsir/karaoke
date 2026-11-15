# -*- coding: utf-8 -*-
"""uicall — 由 worker thread 安全地把 callback 排落 Tk 主線程。

為何需要獨立模組：編輯器、預覽窗、主頁、設定頁都有背景 worker，關窗／
無 mainloop 時 ``widget.after()`` 會拋 ``RuntimeError: main thread is not in
main loop``。以前每個頁面各自複製一份 try/except（有啲 worker 甚至漏咗），
一次燒錄收尾就可以刷一串 traceback。集中在這裡，呼叫方只需一行。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable


def ui_call(widget: Any, fn: Callable[[], None]) -> None:
    """把 ``fn`` 排到 ``widget`` 所屬主線程執行；視窗已關就靜靜放棄。

    Args:
        widget: 任何 Tk widget（提供 ``after``）。
        fn: 要在主線程執行的 callback。**不要**在這裡捕獲 Tcl 物件後再延遲
            使用；需要傳值的請用 default-arg 綁定（``lambda x=x: ...``）。
    """
    try:
        widget.after(0, fn)
    except (RuntimeError, tk.TclError):
        pass
