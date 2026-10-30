# -*- coding: utf-8 -*-
"""pages.settings_page — 設定頁（骨架；commit 3x 填入完整功能）。"""

from __future__ import annotations

import ttkbootstrap as ttkb


class SettingsPage(ttkb.Frame):
    """設定：API key / ffmpeg 路徑 / 預設資料夾 / 主題。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        ttkb.Label(self, text="設定（骨架）", font=("Microsoft JhengHei UI", 14)).pack(pady=40)

    def on_show(self) -> None:
        pass
