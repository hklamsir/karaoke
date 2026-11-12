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

    ⚠️ ``scope="module"``：同一個 pytest session 內反覆 App() / destroy() 會
    撞 Tk（``invalid command name "tcl_findLibrary"``），所以整組測試共用一個。
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
