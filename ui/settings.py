# -*- coding: utf-8 -*-
"""settings — ``~/.karaoke/settings.json`` 讀寫（計劃書 §2.5 E3 / §5.4）。

完全唔入 repo。API key 優先序（同 main.resolve_api_key 一致）：
``--api-key`` > 環境變數 ``DASHSCOPE_API_KEY`` > ``.env`` > ``settings.json``。
UI 寫入 settings.json 嘅值**唔會**蓋過環境變數。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("karaoke.ui.settings")

SETTINGS_DIR = Path.home() / ".karaoke"
SETTINGS_PATH = SETTINGS_DIR / "settings.json"

#: 預設值（第一次啟動 / 欄位缺失時用）
_DEFAULTS: dict[str, Any] = {
    "dashscope_api_key": "",
    "ffmpeg_path": "",
    "ffprobe_path": "",
    "default_input_dir": "",
    "default_output_dir": "",
    "theme": "litera",   # 介面主題：litera（淺色）/ darkly / superhero，見 theme.PALETTES
    #: 主頁樣式面板（ADR-015：主頁設定 = 燒錄權威來源，故要跨 session 記住）
    #: 內容：font_name / font_size / primary_color / secondary_color /
    #:       outline_color / alignment / margin_v / outline_width
    "home_style": {},
    #: 上次揀「純音樂 MV」的資料夾（ADR-020：下次開檔對話框直接開喺度）
    "last_burn_dir": "",
    #: 逐字微調「連動順延」（ADR-026 補記）：改一個字的時間撞到鄰字時，自動
    #: 把後面（或前面）整段推開。預設關 —— 音軌係事實，默默改已對好的字危險。
    "ripple_edit": False,
    #: ADR-032：編輯器預覽解析度（"auto" = 跟 timings.json 的 PlayRes，
    #: 即輸出影片的真實解析度）與縮放（"fit" / "0.5" / "1.0"）
    "preview_res": "auto",
    "preview_zoom": "fit",
    #: 自訂解析度 [闊, 高]（只用於 preview_res="custom"）
    "preview_res_custom": [1920, 1080],
}


def load() -> dict[str, Any]:
    """讀 settings.json；唔存在 / 壞檔就返預設值（唔會拋例外）。"""
    data = dict(_DEFAULTS)
    if SETTINGS_PATH.is_file():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                data.update({k: v for k, v in stored.items() if k in _DEFAULTS})
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning("settings.json 讀唔到（%s），用預設值", exc)
    return data


def save(data: dict[str, Any]) -> None:
    """原子寫入 settings.json（temp → os.replace；R6 對策）。"""
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**_DEFAULTS, **{k: v for k, v in data.items() if k in _DEFAULTS}}
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_PATH)


def resolve_api_key(data: dict[str, Any] | None = None) -> tuple[str, str]:
    """回傳 (api_key, 來源說明)；優先序 env > .env > settings.json。

    注意：呢度**唔會**把 settings.json 值寫入環境變數 —— main.resolve_api_key
    自己會讀 env / .env；UI 只喺兩者都缺先 fallback 去 settings.json。
    """
    env = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if env:
        return env, "環境變數 DASHSCOPE_API_KEY"
    try:   # 同 main 一致：有 python-dotenv 就試 .env
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:   # noqa: BLE001
        pass
    env = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if env:
        return env, ".env"
    key = (data or load()).get("dashscope_api_key", "").strip()
    if key:
        return key, "settings.json"
    return "", "未設定"


def detect_ffmpeg(data: dict[str, Any] | None = None) -> str:
    """FFmpeg 路徑：settings 優先，否則 shutil.which，再否則常見安裝位。"""
    custom = (data or load()).get("ffmpeg_path", "").strip()
    if custom and Path(custom).is_file():
        return custom
    found = shutil.which("ffmpeg")
    if found:
        return found
    for path in (r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                 "/usr/local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"):
        if os.path.isfile(path):
            return path
    return ""


def detect_ffprobe(data: dict[str, Any] | None = None) -> str:
    """FFprobe 路徑：settings 優先，否則同 ffmpeg 同目錄，再 shutil.which。"""
    custom = (data or load()).get("ffprobe_path", "").strip()
    if custom and Path(custom).is_file():
        return custom
    ffmpeg = detect_ffmpeg(data)
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return str(sibling)
    return shutil.which("ffprobe") or ""


def thumbs_cache_dir() -> Path:
    """縮圖快取根目錄（§6.7.4）。"""
    d = SETTINGS_DIR / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d
