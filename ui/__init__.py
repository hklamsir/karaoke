# -*- coding: utf-8 -*-
"""ui — 字幕編輯器 GUI（ttkbootstrap + 自繪 Canvas）。

架構（計劃書 §3.1）：
    app.py        主視窗 + sidebar 導航（主頁/編輯器/設定/幫助）+ 快捷鍵 + 切頁
    pages/        四個頁面
    widgets/      timeline / preview / lyric_tree / overlay 等自繪元件
    state.py      EditorState（文檔 / 選取 / 髒標記）
    commands.py   UndoStack（command pattern）
    audio.py      WAV 解碼 / peak / pygame 播放
    thumbs.py     2fps 縮圖抽取 / 快取
    settings.py   ~/.karaoke/settings.json
    theme.py      ttkbootstrap 主題 + 色票 + 字型

UI ↔ CLI：``import main``（in-process，A2 決定），CLI main.py 行為零變動。
"""

from .version import read_version as _read_version

#: 版本號來自專案根目錄的 ``VERSION`` 檔（唯一真源，見 ``ui/version.py``）
__version__ = _read_version()
