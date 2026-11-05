# -*- coding: utf-8 -*-
"""test_time_spinbox — 逐字微調的 ▲▼ 箭頭（真 Tk，無顯示器就 skip）。

用戶 2026-11-05：「逐字編輯的時間旁沒有了 spinner，之前是有的」。
上一版把 ``ttk.Spinbox`` 換成純 ``Entry`` 就係為咗時間碼 —— 但用家要的是
**箭頭**，唔係 Spinbox 本身。呢個檔案守住：

1. ``TimeSpinbox`` 解析時間碼後加減，唔會靜靜唔郁（``ttk.Spinbox`` 的原罪）
2. 箭頭即時套用、同一個字連撳**合併成一個 undo 步驟**
3. 未選中任何字時箭頭唔准改畫面（否則顯示 0:00.000 但其實冇改到嘢）
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

tk = pytest.importorskip("tkinter")
ttkb = pytest.importorskip("ttkbootstrap")

from ui import models, theme  # noqa: E402
from ui.widgets.time_spinbox import TimeSpinbox  # noqa: E402


@pytest.fixture(scope="module")
def root():
    """真 Tk root（withdraw）；CI 冇顯示器就 skip 而唔係 fail。

    ⚠️ 用 module scope：連續 destroy/create 多個 ``ttkb.Window`` 會令
    ttkbootstrap 的 Style 單例狀態混亂，實測第 3 個窗口就拋 TclError，
    令測試被靜靜 skip（假綠）。
    """
    try:
        win = ttkb.Window(themename="litera")
    except tk.TclError:                     # pragma: no cover - 無顯示環境
        pytest.skip("環境冇可用顯示器，略過 GUI 測試")
    theme.apply_ttkbootstrap_theme(win, "light")
    win.withdraw()
    yield win
    win.destroy()


# ---------------------------------------------------------------------------
# TimeSpinbox 本體
# ---------------------------------------------------------------------------

def test_step_adds_and_subtracts(root) -> None:
    var = tk.StringVar(value="3:41.995")
    spin = TimeSpinbox(root, textvariable=var)
    assert spin.step(+1) == 222_005 and var.get() == "3:42.005"
    assert spin.step(-1) == 221_995 and var.get() == "3:41.995"


def test_step_uses_custom_increment(root) -> None:
    var = tk.StringVar(value="1:00.000")
    spin = TimeSpinbox(root, textvariable=var, step_ms=100)
    assert spin.step(+1) == 60_100


def test_step_clamps_at_zero(root) -> None:
    var = tk.StringVar(value="0:00.005")
    spin = TimeSpinbox(root, textvariable=var)
    assert spin.step(-1) == 0 and var.get() == "0:00.000"


def test_step_from_placeholder_starts_at_zero(root) -> None:
    """未選中時輸入框顯示「—」：解析失敗要由 0 起，而唔係爆 ValueError。"""
    var = tk.StringVar(value="—")
    spin = TimeSpinbox(root, textvariable=var)
    assert spin.step(+1) == 10


def test_callback_can_reject(root) -> None:
    """回呼回傳 None = 唔套用 → 輸入框文字一個字都唔准改。"""
    var = tk.StringVar(value="3:41.995")
    spin = TimeSpinbox(root, textvariable=var, on_step=lambda _ms: None)
    assert spin.step(+1) is None
    assert var.get() == "3:41.995"


def test_callback_can_clamp(root) -> None:
    """回呼回傳夾過的值 → 輸入框要顯示**夾過**的值（唔係試算值）。"""
    var = tk.StringVar(value="3:41.995")
    spin = TimeSpinbox(root, textvariable=var, on_step=lambda _ms: 222_000)
    assert spin.step(+1) == 222_000
    assert var.get() == "3:42.000"


# ---------------------------------------------------------------------------
# 面板整合（用戶回報的那個 regression）
# ---------------------------------------------------------------------------

def _broken_page(root):
    from ui.pages.editor import EditorPage

    page = EditorPage(root)
    page.pack(fill="both", expand=True)
    page._state.doc = {
        "schema_version": 3, "source": {"hold_ms": 300}, "style": {},
        "lines": [{
            "start_ms": 221_995, "end_ms": 233_158, "text": "那裡保證何時能突破",
            "break_reason": "sentence",
            "words": [{"text": "那裡保證何時能突破", "begin_ms": 221_995,
                       "end_ms": 233_158, "estimated": True, "punctuation": "",
                       "chars": [{"text": "那", "begin_ms": 221_995, "end_ms": 222_115},
                                 {"text": "裡", "begin_ms": 222_115, "end_ms": 222_235}]}],
        }],
    }
    page._state.path = Path("_test_.json")
    page._state.notify("load")
    root.update()
    return page


@pytest.fixture()
def page(root):
    """每個測試一個乾淨的編輯器頁（用完即 destroy，唔會疊埋一齊）。"""
    p = _broken_page(root)
    yield p
    p.destroy()


def test_panel_has_time_spinners(page, root) -> None:
    """面板兩個時間欄都要有箭頭（唔可以再變返純 Entry），而且整行唔准超出左欄。

    ⚠️ 唔用 ``winfo_ismapped()``：root withdrawn 之後所有子 widget 都報 0，
    同「被裁」分唔開。改用 ``winfo_reqwidth()`` 對左欄可用寬度 —— 呢個係
    「加咗掣之後會唔會爆格」的真正判據（2026-10 用戶試過冇咗「確定」掣）。
    """
    from ui.pages.editor import LEFT_W

    assert isinstance(page._begin_spin, TimeSpinbox)
    assert isinstance(page._end_spin, TimeSpinbox)
    # 箭頭係 Canvas 畫出嚟（唔靠 ▲▼ 字元 —— 用戶 2026-11-05：字元版本
    # 喺 Windows fallback 咗去另一個字型，出到嚟唔係三角形）
    for spin in (page._begin_spin, page._end_spin):
        assert spin._canvas.winfo_manager() == "pack"
        polys = [i for i in spin._canvas.find_all()
                 if spin._canvas.type(i) == "polygon"]
        assert len(polys) == 2, "上下箭頭（多邊形）畫唔到"

    row = page._begin_spin.master                     # 開始／結束 那一行
    assert row.winfo_reqwidth() <= LEFT_W - 8, (
        f"時間欄那一行需要 {row.winfo_reqwidth()}px，超出左欄可用寬度")


def test_spin_edge_applies_and_merges_undo(page, root) -> None:
    page._state.set_selection((0, 0, 0))
    root.update()
    char = models.char_of(page._state.lines[0], 0, 0)
    start = char["begin_ms"]

    for _ in range(3):
        page._begin_spin.step(+1)
        root.update()

    char = models.char_of(page._state.lines[0], 0, 0)
    assert char["begin_ms"] == start + 30
    assert len(page._state.undo_stack._undo) == 1        # 三次合併成一步

    page._state.undo()
    char = models.char_of(page._state.lines[0], 0, 0)
    assert char["begin_ms"] == start
    assert page._state.lines[0]["words"][0]["estimated"] is True   # 旗標要還原


def test_spin_edge_never_crosses_the_other_edge(page, root) -> None:
    page._state.set_selection((0, 0, 0))
    root.update()
    char = models.char_of(page._state.lines[0], 0, 0)
    end = char["end_ms"]

    for _ in range(20):                                   # 一路加，會撞到 end
        page._begin_spin.step(+1)
    char = models.char_of(page._state.lines[0], 0, 0)
    assert char["begin_ms"] == end - 1                    # 夾到 end-1，唔會反轉


# ---------------------------------------------------------------------------
# 連動順延（用戶 2026-11-05 提問）
# ---------------------------------------------------------------------------

def test_ripple_off_leaves_neighbour_alone(page, root) -> None:
    """預設關：改「那」的 end 越過「裡」的 begin，鄰字一個 ms 都唔准郁。"""
    page._ripple_var.set(False)
    page._state.set_selection((0, 0, 0))
    root.update()
    neighbour_before = models.char_of(page._state.lines[0], 0, 1)["begin_ms"]

    page._spin_edge("end", 222_165)          # 越過 裡.begin(222115) 50ms

    assert models.char_of(page._state.lines[0], 0, 0)["end_ms"] == 222_165
    assert models.char_of(page._state.lines[0], 0, 1)["begin_ms"] == neighbour_before
    assert "重疊" in page._ft_hint_var.get()     # 要話畀用戶知，唔好靜靜雞


def test_ripple_after_shifts_tail(page, root) -> None:
    """連動開：後面整段順延，字距保持，一個 undo 全部還原。"""
    page._ripple_var.set(True)
    page._state.set_selection((0, 0, 0))
    root.update()
    tail_before = copy.deepcopy(models.char_of(page._state.lines[0], 0, 1))
    tail_dur = tail_before["end_ms"] - tail_before["begin_ms"]

    page._spin_edge("end", 222_165)          # 越過 50ms

    tail = models.char_of(page._state.lines[0], 0, 1)
    assert tail["begin_ms"] == 222_165       # 貼住新的 end
    assert tail["end_ms"] - tail["begin_ms"] == tail_dur   # 字距不變
    assert "順延" in page._ft_hint_var.get()

    assert len(page._state.undo_stack._undo) == 1
    page._state.undo()
    assert models.char_of(page._state.lines[0], 0, 1)["begin_ms"] == tail_before["begin_ms"]


def test_ripple_before_shifts_head(page, root) -> None:
    """連動開：把後一個字的開始拉前，越過前一個字的結束 → 前面整段向前移。"""
    page._ripple_var.set(True)
    page._state.set_selection((0, 0, 1))     # 裡
    root.update()
    head_before = copy.deepcopy(models.char_of(page._state.lines[0], 0, 0))
    head_dur = head_before["end_ms"] - head_before["begin_ms"]

    page._spin_edge("begin", 222_005)        # 早過 那.end(222115) 110ms

    head = models.char_of(page._state.lines[0], 0, 0)
    assert head["end_ms"] == 222_005         # 貼住新的 begin
    assert head["end_ms"] - head["begin_ms"] == head_dur
    assert models.char_of(page._state.lines[0], 0, 1)["begin_ms"] == 222_005


def test_ripple_never_pushes_before_zero(page, root) -> None:
    """最極端情況：前面已經貼住 0 → 位移要縮細，唔可以出負數。"""
    page._ripple_var.set(True)
    chars = models.iter_flat_chars(page._state.lines[0])
    chars[0][2]["begin_ms"], chars[0][2]["end_ms"] = 0, 100
    page._state.set_selection((0, 0, 1))
    root.update()

    page._spin_edge("begin", 0)               # 想拉到 0，會撞到 head 的 100

    assert models.char_of(page._state.lines[0], 0, 0)["begin_ms"] == 0
    assert models.char_of(page._state.lines[0], 0, 1)["begin_ms"] >= 1
