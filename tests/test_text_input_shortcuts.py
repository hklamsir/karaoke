# -*- coding: utf-8 -*-
"""全域快捷鍵 vs 文字輸入控件的分流測試（ADR-041）。

用戶回報：喺逐字面板「開始」時間框按右箭咀，會連句子一齊跳到下一句，游標亦
被搶走。根因是 ``App.bind`` 綁喺 toplevel，而 Tk 的 bindtags 次序是
「widget → class → toplevel」，Entry 的 class binding 移動完游標之後，
toplevel 的全域 handler 仍然會執行。

`is_text_input()` 就是這個分流判斷；本檔鎖住它認得哪些控件，以及不會誤判
（樹狀清單、畫布等仍然要收到全域快捷鍵）。
"""
from __future__ import annotations

import tkinter as tk

import pytest

from ui.app import is_text_input


class _FakeWidget:
    """最小 widget：只需要 ``winfo_class()``。"""

    def __init__(self, cls: str, *, boom: bool = False) -> None:
        self._cls, self._boom = cls, boom

    def winfo_class(self) -> str:
        if self._boom:
            raise tk.TclError("application has been destroyed")
        return self._cls


class _FakeEvent:
    def __init__(self, widget: object | None) -> None:
        self.widget = widget


@pytest.mark.parametrize("cls", ["Entry", "TEntry", "Spinbox", "TSpinbox",
                                 "TCombobox", "Text"])
def test_text_widgets_are_detected(cls: str) -> None:
    """Entry / Spinbox / Combobox / Text 一律當作文字輸入（讓路）。"""
    assert is_text_input(_FakeEvent(_FakeWidget(cls))) is True


@pytest.mark.parametrize("cls", ["Treeview", "Canvas", "TFrame", "TButton", "TLabel"])
def test_other_widgets_still_receive_shortcuts(cls: str) -> None:
    """樹狀清單／畫布／按鈕唔可以誤判成輸入框，否則 ←→ 會失效。"""
    assert is_text_input(_FakeEvent(_FakeWidget(cls))) is False


def test_none_event_is_not_text_input() -> None:
    """程式化呼叫（冇 event）→ 照常執行快捷鍵。"""
    assert is_text_input(None) is False


def test_destroyed_widget_does_not_raise() -> None:
    """控件已銷毀（例如換頁）唔應該拋 TclError 令整個按鍵處理爆掉。"""
    assert is_text_input(_FakeEvent(_FakeWidget("TEntry", boom=True))) is False


def test_event_without_widget_attribute() -> None:
    """非 Tk 事件物件（測試 stub）→ 安全回 False。"""
    assert is_text_input(object()) is False
