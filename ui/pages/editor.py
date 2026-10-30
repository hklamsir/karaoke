# -*- coding: utf-8 -*-
"""pages.editor — 字幕編輯器（骨架；commit 5-9 填入完整功能）。"""

from __future__ import annotations

import ttkbootstrap as ttkb


class EditorPage(ttkb.Frame):
    """字幕編輯器：歌詞樹 / 時間軸 / 音訊 / 大 preview 窗 / 逐字微調。"""

    def __init__(self, master) -> None:
        super().__init__(master, bootstyle="dark")
        ttkb.Label(self, text="編輯器（骨架）", font=("Microsoft JhengHei UI", 14)).pack(pady=40)

    def on_show(self) -> None:
        pass

    def on_hide(self) -> None:
        pass
