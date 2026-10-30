# -*- coding: utf-8 -*-
"""pages.home — 主頁（骨架；commit 4 填入完整功能）。"""

from __future__ import annotations

import ttkbootstrap as ttkb


class HomePage(ttkb.Frame):
    """主頁：新任務 / 開啟現有 / 三層屬性面板 / 樣式預覽 / 分階段進度。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        ttkb.Label(self, text="主頁（骨架）", font=("Microsoft JhengHei UI", 14)).pack(pady=40)

    def on_show(self) -> None:      # hook：切到呢頁時刷新
        pass
