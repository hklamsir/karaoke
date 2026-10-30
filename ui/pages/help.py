# -*- coding: utf-8 -*-
"""pages.help — 幫助頁（骨架；commit 10 填入完整功能）。"""

from __future__ import annotations

import ttkbootstrap as ttkb


class HelpPage(ttkb.Frame):
    """幫助：UI + CLI 完整使用方法（章節錨點 + 搜尋 + CLI 動態參數表）。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        ttkb.Label(self, text="幫助（骨架）", font=("Microsoft JhengHei UI", 14)).pack(pady=40)
