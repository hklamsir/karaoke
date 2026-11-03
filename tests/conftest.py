# -*- coding: utf-8 -*-
"""pytest 共用設定：sys.path、fixtures、共用 helper。

測試設計原則（對應 docs/代碼審查報告.html §9）：
- **唔依賴 working directory**：所有路徑由 ``__file__`` 推導（修正 §2.2 的 ``_smoke`` 綁死）
- **唔依賴網絡 / ffmpeg / Tk**：外部程序一律 monkeypatch（CI 唔裝 ffmpeg 都要跑得）
- **唔依賴真實歌曲素材**：fixture 全部為合成資料（ADR-023）
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
FIXTURES_DIR = TESTS_DIR / "fixtures"

#: 令 ``import main`` / ``from fixtures.builder import ...`` 在任何 cwd 下都成立
for _p in (str(PROJECT_ROOT), str(TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(scope="session")
def sample_doc() -> dict[str, Any]:
    """``tests/fixtures/sample_v3.json`` 的唯讀內容（session 級，避免重複讀檔）。"""
    return json.loads((FIXTURES_DIR / "sample_v3.json").read_text(encoding="utf-8"))


@pytest.fixture
def doc(sample_doc: dict[str, Any]) -> dict[str, Any]:
    """``sample_doc`` 的可變副本 —— 測試可以放心改，唔會污染其他測試。"""
    return copy.deepcopy(sample_doc)


@pytest.fixture
def dialogues():
    """解析 ASS 文字中的 Dialogue 事件，回傳結構化列表。

    Returns:
        list[dict]：``start_ms`` / ``end_ms`` / ``text`` / ``raw`` 四個鍵。
    """

    def _parse(ass_text: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for line in ass_text.splitlines():
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",", 9)
            out.append({
                "start_ms": _ass_time_to_ms(parts[1]),
                "end_ms": _ass_time_to_ms(parts[2]),
                "text": parts[9],
                "raw": line,
            })
        return out

    return _parse


def _ass_time_to_ms(value: str) -> int:
    """``H:MM:SS.cc`` → 毫秒（測試側獨立實作，唔重用被測程式碼，避免自我驗證）。

    Args:
        value: ASS 時間戳。

    Returns:
        毫秒數。
    """
    clock, cs = value.strip().split(".")
    hours, minutes, seconds = (int(x) for x in clock.split(":"))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + int(cs) * 10


@pytest.fixture
def ass_time_to_ms():
    """把 ``_ass_time_to_ms`` 暴露給測試檔使用。"""
    return _ass_time_to_ms


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict[str, Path]:
    """整套 ffmpeg 合成影音素材（session 級，整個 run 只生成一次）。

    素材入庫與否的取捨見 ``tests/fixtures/media.py`` 檔頭：合成物唔入庫，
    即場重生。無 ffmpeg 的機器整批 skip（可用 ``KARAOKE_FFMPEG`` 指定路徑）。
    """
    from fixtures.media import build_all, ffmpeg_path

    if not ffmpeg_path():
        pytest.skip("本機無 ffmpeg —— 跳過需要影音素材的測試"
                    "（可用環境變數 KARAOKE_FFMPEG 指定 ffmpeg 路徑）")
    workdir = tmp_path_factory.mktemp("media")
    return build_all(workdir)


@pytest.fixture(scope="session")
def ffprobe() -> str:
    """ffprobe 路徑（無則 skip）。"""
    from fixtures.media import ffprobe_path

    found = ffprobe_path()
    if not found:
        pytest.skip("本機無 ffprobe —— 跳過需要探測媒體的測試")
    return found
