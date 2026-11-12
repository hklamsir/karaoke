# -*- coding: utf-8 -*-
"""頁面切換與播放／重繪的互動測試（ADR-045）。

用戶回報：播放期間整個 App 反應遲緩，跳去主頁時尤其嚴重。原因是播放期間
``notify("cursor")`` 每 30ms 一次，連隱藏頁面的預覽 canvas 都在重畫。

本檔鎖住三件事：
1. 切走編輯器要自動暫停（游標停在當前位置，切返可以續播）
2. 隱藏頁面時預覽唔重繪
3. ``EditorPage.toggle_play`` 是公開方法（App 的空白鍵靠 ``hasattr`` 查找）
"""
from __future__ import annotations

import inspect

import pytest

pytest.importorskip("tkinter")
pytest.importorskip("ttkbootstrap")


@pytest.fixture(scope="module")
def app():
    """建立完整 App；Tk 不可用（CI 無 display）就 skip。

    ⚠️ 整個測試 session 只能有一個 ``ttkb.Window``：反覆建立／銷毀會令
    Tcl 解釋器殘留（``invalid command name "tcl_findLibrary"``），所以本檔
    所有 UI 整合測試（含下面的解析度同步）共用這一個。
    """
    from ui.app import App

    try:
        instance = App()
    except Exception as exc:      # noqa: BLE001 — 無 X server 等
        pytest.skip(f"Tk 不可用：{exc}")
    yield instance
    try:
        instance.destroy()
    except Exception:             # noqa: BLE001
        pass


class _FakeEngine:
    """最小播放引擎：只記錄 playing / 位置 / toggle 次數。"""

    available = True
    playback_degraded = False

    def __init__(self, *, playing: bool = False, pos_ms: int = 0) -> None:
        self.playing = playing
        self.pos_ms = pos_ms
        self.toggles = 0

    def position_ms(self) -> int:
        return self.pos_ms

    def toggle(self, cursor_ms: int) -> bool:
        self.toggles += 1
        self.playing = not self.playing
        return self.playing


def test_editor_exposes_public_toggle_play() -> None:
    """App._dispatch("toggle_play") 用 hasattr 查找，缺公開方法就靜默失效。"""
    from ui.pages.editor import EditorPage

    assert hasattr(EditorPage, "toggle_play")
    source = inspect.getsource(EditorPage.toggle_play)
    assert "_toggle_play" in source


def test_on_hide_pauses_playback(app) -> None:
    """播放中切走頁面 → 停播，游標停在當前播放位置。"""
    editor = app.page("editor")
    engine = _FakeEngine(playing=True, pos_ms=1234)
    editor._audio = engine

    editor.on_hide()

    assert engine.playing is False
    assert engine.toggles == 1
    assert editor._state.cursor_ms == 1234


def test_on_hide_does_not_touch_engine_when_idle(app) -> None:
    """冇播放就唔好亂 call toggle（會變成開始播）。"""
    editor = app.page("editor")
    engine = _FakeEngine(playing=False)
    editor._audio = engine

    editor.on_hide()

    assert engine.toggles == 0
    assert engine.playing is False


def test_preview_skips_redraw_when_editor_hidden(app) -> None:
    """切去主頁之後，cursor 更新唔應該再重畫預覽。"""
    editor = app.page("editor")
    preview = editor._preview
    app.show_page("home")

    calls: list[int] = []
    original = preview._redraw_video
    preview._redraw_video = lambda: calls.append(1)
    try:
        preview._on_state_change("cursor")
        assert calls == []
    finally:
        preview._redraw_video = original


def test_preview_redraws_when_editor_visible(app) -> None:
    """切返編輯器之後，cursor 更新要正常重畫。"""
    editor = app.page("editor")
    preview = editor._preview
    app.show_page("editor")

    calls: list[int] = []
    original = preview._redraw_video
    preview._redraw_video = lambda: calls.append(1)
    try:
        preview._on_state_change("cursor")
        assert len(calls) == 1
    finally:
        preview._redraw_video = original


def test_on_show_redraws_once(app) -> None:
    """切返編輯器要補一次重繪（隱藏期間漏掉的畫面）。"""
    editor = app.page("editor")
    preview = editor._preview
    calls: list[int] = []
    original = preview._redraw_video
    preview._redraw_video = lambda: calls.append(1)
    try:
        editor.on_show()
        assert len(calls) >= 1
    finally:
        preview._redraw_video = original


# ---------------------------------------------------------------------------
# 預覽「解析度」= 字幕基準（ADR-046）
# ---------------------------------------------------------------------------

@pytest.fixture
def preview(app, sample_doc, tmp_path):
    """載入 fixture doc 的預覽面板（不經 load_timings，避免 probe 真實影片）。"""
    import json

    path = tmp_path / "sample_timings.json"
    path.write_text(json.dumps(sample_doc, ensure_ascii=False), encoding="utf-8")

    editor = app.page("editor")
    editor._state.load(path)
    pane = editor._preview
    pane.set_source_size(1920, 1080)
    yield pane
    style = editor._state.doc["style"]
    style["play_res_x"], style["play_res_y"] = 1920, 1080


def _play_res(preview) -> tuple[int, int]:
    style = preview._state.doc["style"]
    return int(style["play_res_x"]), int(style["play_res_y"])


def test_choosing_720p_rewrites_play_res(preview) -> None:
    """選 1280×720 → PlayRes 跟着改（字級 px 不變）。"""
    preview._res_var.set("1280×720")
    preview._sync_play_res("1280x720")

    assert _play_res(preview) == (1280, 720)
    assert int(preview._state.doc["style"]["font_size"]) == 48, "字級唔應該被改"


def test_back_to_source_restores_play_res(preview) -> None:
    """再選「跟原片」→ 回到原片真實尺寸。"""
    preview._res_var.set("1280×720")
    preview._sync_play_res("1280x720")
    assert _play_res(preview) == (1280, 720)

    preview._res_var.set("跟原片")
    preview._sync_play_res("auto")
    assert _play_res(preview) == (1920, 1080)


def test_same_resolution_is_noop(preview) -> None:
    """已經同一組值 → 唔應該再標 dirty（避免每次開選單都改檔）。"""
    preview._res_var.set("1920×1080")
    preview._state.dirty = False
    preview._sync_play_res("1920x1080")
    assert _play_res(preview) == (1920, 1080)
    assert preview._state.dirty is False


def test_frame_aspect_ignores_resolution(preview) -> None:
    """畫面比例永遠跟原片：選 720p 前後，fw/fh 比例不變。"""
    preview.set_source_size(1920, 1080)
    _x, _y, fw1, fh1, _s1 = preview._frame_geometry(1000, 500)

    preview._res_var.set("1280×720")
    preview._sync_play_res("1280x720")
    _x, _y, fw2, fh2, _s2 = preview._frame_geometry(1000, 500)

    assert fw1 / fh1 == pytest.approx(16 / 9)
    assert fw2 / fh2 == pytest.approx(16 / 9)


def test_43_source_stays_43_after_res_change(preview) -> None:
    """4:3 原片即使選 1920×1080，畫面仍然 4:3（只改字幕基準）。"""
    preview.set_source_size(640, 480)
    preview._res_var.set("1920×1080")
    preview._sync_play_res("1920x1080")
    _x, _y, fw, fh, _s = preview._frame_geometry(1000, 1000)
    assert fw / fh == pytest.approx(4 / 3)
    assert _play_res(preview) == (1920, 1080)
