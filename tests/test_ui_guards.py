# -*- coding: utf-8 -*-
"""§2.4 / §2.5 —— UI 層兩個「按了按鈕冇反應」級別的 P0 缺陷。

這兩條都有一個共同點：**錯誤不會出現在畫面上**。

- §2.4 ``_poll_position`` 的 after-chain 每次載入音訊都多一條，永不停止。
  症狀是「用久越來越卡」，用戶只會覺得程式慢，不會知道有三條 30ms 鏈在跑。
- §2.5 ``_build_options`` 用裸 ``int()`` 讀 Spinbox。用戶清空欄位再按
  「開始生成」，``ValueError`` 在 Tk callback 內被 tkinter 印到 stderr，
  畫面完全冇反應、冇錯誤訊息。

兩者都不需要開真視窗：把待測方法綁到一個輕量 stub 上，用假的
``after()`` 記錄排程、用假的 ``StringVar`` 餵值，就可在 CI 上決定性重現。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("ttkbootstrap")          # ui.pages.* 在 module level import 它

import main
from ui.pages.editor import EditorPage
from ui.pages.home import HomePage

# ---------------------------------------------------------------------------
# §2.4 · 游標輪詢鏈只准一條
# ---------------------------------------------------------------------------


class _Btn(dict):
    """假的 ttk Button：支援 ``btn["text"]`` 與 ``btn.configure(text=...)``。"""

    def configure(self, **kwargs) -> None:   # noqa: ANN003
        self.update(kwargs)


class _PollPage:
    """輕量 stub：只提供 ``_poll_position`` / ``_ensure_polling`` 用到的屬性。"""

    _polling = False

    def __init__(self, engine) -> None:      # noqa: ANN001
        self._audio = engine
        self._state = SimpleNamespace(set_cursor=lambda ms: None)
        self._play_btn = _Btn(text="▶")
        self.scheduled: list[int] = []
        # 把真方法綁上 stub（唔用繼承，避免 ttkb.Frame 的 __init__）
        self._poll_position = EditorPage._poll_position.__get__(self)
        self._ensure_polling = EditorPage._ensure_polling.__get__(self)

    def after(self, ms: int, callback) -> str:   # noqa: ANN001
        """唔真排程，只記錄 —— 測試要的是「排了幾多次」，不是真的等 30ms。"""
        self.scheduled.append(ms)
        return f"after#{len(self.scheduled)}"


def _playing_engine() -> SimpleNamespace:
    return SimpleNamespace(available=True, playing=False, position_ms=lambda: 0)


def test_ensure_polling_is_idempotent() -> None:
    """連續 3 次（= 開 3 個檔）只可以起 1 條鏈。

    以前 ``_on_audio_ready`` 每次都直接叫 ``_poll_position()``，每次都排一條
    永不停止的 30ms 鏈 —— 3 個檔 = 3 條鏈 = 每 30ms 三次全量重繪。
    """
    page = _PollPage(_playing_engine())
    for _ in range(3):
        page._ensure_polling()

    assert len(page.scheduled) == 1, f"起了 {len(page.scheduled)} 條鏈（應該只有 1 條）"
    assert page._polling is True


def test_poll_chain_self_terminates_and_can_restart() -> None:
    """引擎消失 → 鏈條結束並清旗標；之後再載入音訊仍然可以重新啟動。

    若只在 ``__init__`` 設一次 ``_polling = True`` 而唔清，換片後就永遠
    唔會再更新游標（另一種 bug：按播放畫面不動）。
    """
    page = _PollPage(None)
    page._ensure_polling()
    assert page.scheduled == [], "冇引擎時唔應該排程"
    assert page._polling is False, "鏈條結束時必須清旗標，否則永遠唔會重啟"

    page._audio = _playing_engine()
    page._ensure_polling()
    assert len(page.scheduled) == 1
    assert page._polling is True


def test_poll_chain_reschedules_only_once_per_tick() -> None:
    """每次 tick 只准排一次下一次 —— 防止有人「順手」加多個 after 造成雙倍頻率。"""
    page = _PollPage(_playing_engine())
    page._ensure_polling()
    page._poll_position()        # 模擬 timer 到點
    assert len(page.scheduled) == 2
    assert set(page.scheduled) == {30}


# ---------------------------------------------------------------------------
# §2.5 · 主頁進階參數防呆
# ---------------------------------------------------------------------------


class _Var:
    """假的 tk StringVar / BooleanVar：只需要 ``get()``。"""

    def __init__(self, value) -> None:       # noqa: ANN001
        self._value = value

    def get(self):                           # noqa: ANN201
        return self._value


_STYLE_SNAPSHOT = {
    "font_name": "Microsoft JhengHei",
    "font_size": 48,
    "primary_color": "#FFFFFF",
    "secondary_color": "#00FFFF",
    "outline_color": "#000000",
    "alignment": 2,
    "outline_width": 2,
    "margin_v": 40,
}


def _home_stub(tmp_path: Path, spin_values: dict[str, str] | None = None):
    """假的主頁：只提供 ``_build_options`` 會讀到的屬性。

    真 ``HomePage`` 需要 Tk root 才建得起來（CI 上會 TclError），而這裡要驗的
    純粹是「數值解析 + 組 options」這段邏輯，同 widget 生命週期無關。
    """
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"fake")

    keys = ("max_chars", "gap_threshold_ms", "hold_ms",
            "min_clause_chars", "min_clause_ms", "merge_max_chars")
    values = {key: "" for key in keys}        # 預設全部清空 —— 正是觸發 bug 的輸入
    values.update(spin_values or {})

    page = SimpleNamespace(
        _video_var=_Var(str(video)),
        _output_var=_Var(""),
        _model_var=_Var(main.DEFAULT_ASR_MODEL),
        _lang_var=_Var(None),
        _separate_var=_Var(False),
        _adv_spins={key: _Var(values[key]) for key in keys},
        _keep_punct_var=_Var(False),
        _save_raw_var=_Var(False),
        master=SimpleNamespace(settings_data={}),
    )
    page._safe_int = HomePage._safe_int
    page._adv_int = lambda key, default: HomePage._adv_int(page, key, default)
    page.style_snapshot = lambda: dict(_STYLE_SNAPSHOT)
    return page


def test_build_options_survives_cleared_spinboxes(tmp_path: Path) -> None:
    """Spinbox 全部清空 → 用預設值，唔可以拋 ValueError（§2.5 P0）。

    以前 ``int("")`` 在 Tk callback 內拋錯 → 按鈕「冇反應」、零錯誤提示。
    """
    opts = HomePage._build_options(_home_stub(tmp_path))

    assert opts is not None
    assert opts.max_chars == 18
    assert opts.gap_threshold_ms == 800
    assert opts.hold_ms == 300
    assert opts.min_clause_chars == 5
    assert opts.min_clause_ms == 2500
    assert opts.merge_max_chars == 16


def test_build_options_survives_non_numeric_spinboxes(tmp_path: Path) -> None:
    """貼上非數字（例如 "abc"、"1.5"）同樣回預設，唔可以拋錯。"""
    page = _home_stub(tmp_path, {
        "max_chars": "abc",
        "gap_threshold_ms": "1.5",
        "hold_ms": "  ",
        "min_clause_chars": "三",
        "min_clause_ms": "",
        "merge_max_chars": "--",
    })

    opts = HomePage._build_options(page)
    assert opts is not None
    assert (opts.max_chars, opts.gap_threshold_ms, opts.hold_ms) == (18, 800, 300)
    assert (opts.min_clause_chars, opts.min_clause_ms, opts.merge_max_chars) == (5, 2500, 16)


def test_build_options_keeps_valid_values(tmp_path: Path) -> None:
    """合法值必須原樣保留 —— 防「防呆寫過頭，全部變預設」。"""
    page = _home_stub(tmp_path, {"max_chars": "24", "hold_ms": "150"})
    opts = HomePage._build_options(page)

    assert opts is not None
    assert opts.max_chars == 24
    assert opts.hold_ms == 150
    assert opts.gap_threshold_ms == 800        # 其餘仍是預設


def test_build_options_returns_none_without_video(tmp_path: Path, monkeypatch) -> None:
    """未揀片 → 回 None（由呼叫方顯示提示），唔可以拋錯。

    ``messagebox`` 需要真 Tk root，所以這裡只斷言「有被叫過」。
    """
    from ui.pages import home as home_module

    warned: list[str] = []
    monkeypatch.setattr(home_module.messagebox, "showwarning",
                        lambda title, msg, **kw: warned.append(title))

    page = _home_stub(tmp_path)
    page._video_var = _Var(str(tmp_path / "missing.mp4"))

    assert HomePage._build_options(page) is None
    # ADR-039：來源可以是影片或音檔，所以標題由「未選擇影片」改為「未選擇來源」
    assert warned == ["未選擇來源"]


def test_build_options_parses_style_snapshot(tmp_path: Path) -> None:
    """樣式快照要完整傳入 GenerateOptions（曾經漏 alignment / outline_width）。"""
    opts = HomePage._build_options(_home_stub(tmp_path))

    assert opts is not None
    assert opts.style.font_size == 48
    assert opts.style.alignment == 2
    assert opts.style.outline_width == 2
    assert opts.style.margin_v == 40


# ---------------------------------------------------------------------------
# §2.3 · ThumbCache.cancelled 接線（原本是死代碼）
# ---------------------------------------------------------------------------


def test_start_thumbs_cancels_previous_extraction() -> None:
    """換片要叫停上一條抽縮圖 thread。

    審查報告原文：「``ThumbCache.cancelled`` 屬性存在且有檢查邏輯，但全 codebase
    無一處將它設為 True，是未接線的半成品。」—— 這條測試鎖的就是「有冇接線」：
    舊 task 的 ``cancelled`` 必須被設 True，否則開新片後舊 thread 仍然在抽
    幾百張 PNG，同新 thread 爭 disk I/O。
    """
    from ui.widgets.preview import PreviewPane

    class _Task:
        cancelled = False

    class _Var:
        def __init__(self) -> None:
            self.value: str | None = None

        def set(self, value: str) -> None:
            self.value = value

    task = _Task()
    stub = SimpleNamespace(
        _thumbs_task=task,
        _thumbs=object(),                 # 模擬「已有完成品」
        _thumb_progress_var=_Var(),
    )

    # video=None → 只走「清理舊狀態」那段就 return，唔會碰 ffprobe / Tk
    PreviewPane.start_thumbs(stub, None, "ffmpeg", "ffprobe")

    assert task.cancelled is True, "舊的縮圖抽取冇被叫停 → cancelled 仍然是死代碼"
    assert stub._thumbs_task is None
    assert stub._thumbs is None
    assert "無法顯示預覽" in (stub._thumb_progress_var.value or "")
