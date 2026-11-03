# -*- coding: utf-8 -*-
"""9.2 第 11 / 12 項 —— 任務取消與重複提交防護。

對應 docs/代碼審查報告.html：
- 9.2-11 任務取消（ADR-025 已實作：``GenerateOptions.cancel_event`` 貫穿
  抽音／上傳／ASR 輪詢／斷句／寫檔；燒錄改用 Popen + terminate）
- 9.2-12 重複提交相同任務（ADR-025 已實作：``main.SingleFlight``，
  UI 的 ``_running`` 布林旗標已移除）

這些測試原本是 ``xfail(strict=True)`` 契約（未實作時 xfail、實作完成後
XPASS 會令 suite 變紅逼人拆標記）。功能已落地，標記已拆 —— 現在它們是
普通回歸測試，任何一次改動破壞取消語義都會直接 FAILED。

為何唔測燒錄取消：燒錄屬 ffmpeg 外部程序生命週期（Popen + terminate），
要真影片才測得穩；本檔只鎖「ASR 輪詢可被 cancel_event 中斷」這個
9.2 明文要求的量化契約（< 2 個 poll 週期內拋出）。燒錄取消的
「半截輸出檔要刪除」由 ``test_burn_cancel.py`` 以假 ffmpeg 驗。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import main

# ---------------------------------------------------------------------------
# 9.2-11 · 取消
# ---------------------------------------------------------------------------


def test_generate_options_has_cancel_event(tmp_path: Path) -> None:
    """``GenerateOptions`` 應自帶未觸發的 ``cancel_event``（UI 可無條件 ``.set()``）。"""
    opts = main.GenerateOptions(input=tmp_path / "x.mp4")
    assert isinstance(opts.cancel_event, threading.Event)
    assert opts.cancel_event.is_set() is False


def test_cancel_stops_polling_within_two_cycles(monkeypatch) -> None:
    """``cancel_event.set()`` 後必須在 2 個 poll 週期內拋 ``TaskCancelled``。

    以 ``fetch`` 呼叫次數量化「< 2 個 poll 週期」：第一次 poll 後即取消，
    最多只應該再 poll 一次（= 2 次），唔可以繼續等 30 分鐘上限。
    """
    from dashscope.audio.asr import Transcription

    calls: list[str] = []

    class _FakeResp:
        status_code = 200
        output = {"task_id": "t-1"}

    class _FakeFetch:
        output = {"task_status": "RUNNING"}

    monkeypatch.setattr(Transcription, "async_call", lambda **kwargs: _FakeResp())

    def fake_fetch(task):                       # noqa: ANN001
        calls.append(task)
        return _FakeFetch()

    monkeypatch.setattr(Transcription, "fetch", fake_fetch)

    cancel = threading.Event()

    def cancel_soon() -> None:
        for _ in range(500):                    # 最多等 0.5s，避免卡死
            if calls:
                cancel.set()
                return
            time.sleep(0.001)
        cancel.set()

    watcher = threading.Thread(target=cancel_soon, daemon=True)
    watcher.start()

    with pytest.raises(main.TaskCancelled):
        main.submit_and_wait_transcription(
            model=main.DEFAULT_ASR_MODEL,
            uploaded_url="https://example.invalid/a.wav",
            language_hints=None,
            poll_interval_s=0.01,
            cancel_event=cancel,
        )

    watcher.join(timeout=1.0)
    assert len(calls) <= 3, f"取消後仍 poll 了 {len(calls)} 次，取消未被檢查"


def test_task_cancelled_is_not_karaoke_error() -> None:
    """取消唔算「錯誤」：``TaskCancelled`` 不應繼承 ``KaraokeError``。

    否則 CLI 入口會把它當失敗並回非零 exit code，令「用戶主動取消」變成「程式出錯」。
    """
    assert not issubclass(main.TaskCancelled, getattr(main, "KaraokeError", Exception))
    assert issubclass(main.TaskCancelled, Exception)


# ---------------------------------------------------------------------------
# 9.2-12 · 重複提交
# ---------------------------------------------------------------------------


def test_single_flight_blocks_second_acquire() -> None:
    """第二次 ``acquire()`` 必須回 False（= 「已經有任務跑緊」）。"""
    guard = main.SingleFlight()
    assert guard.acquire() is True
    assert guard.acquire() is False
    guard.release()
    assert guard.acquire() is True


def test_single_flight_context_manager_releases_on_error() -> None:
    """例外路徑必須自動釋放，否則一次失敗就永久鎖死後續任務。"""
    guard = main.SingleFlight()
    with pytest.raises(RuntimeError):
        with guard:
            raise RuntimeError("模擬任務中途出錯")
    assert guard.acquire() is True, "例外後 guard 未釋放 → 任務永久鎖死"


def test_single_flight_is_thread_safe() -> None:
    """8 條 thread 同時搶 → 只可以有 1 條成功（``_running`` 旗標做不到這點）。"""
    guard = main.SingleFlight()
    barrier = threading.Barrier(8)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        ok = guard.acquire()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 8
    assert sum(1 for r in results if r) == 1


def test_cancel_event_is_per_instance(tmp_path: Path) -> None:
    """``default_factory`` 必須每次新建 Event —— 共用一個會令「上次取消」殘留。

    如果寫成 ``cancel_event: threading.Event = threading.Event()``（class-level
    預設值），所有 GenerateOptions 會共用同一個 Event：取消一次之後，之後每次
    generate 一開波就即刻被取消，而且極難查。
    """
    a = main.GenerateOptions(input=tmp_path / "a.mp4")
    b = main.GenerateOptions(input=tmp_path / "b.mp4")
    assert a.cancel_event is not b.cancel_event

    a.cancel_event.set()
    assert b.cancel_event.is_set() is False


def test_generate_aborts_immediately_when_already_cancelled(tmp_path: Path) -> None:
    """已取消的任務唔應該開始：第一個檢查點就要拋，唔可以仲去抽音／上網。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"fake")
    cancel = threading.Event()
    cancel.set()

    opts = main.GenerateOptions(input=video, api_key="sk-test", cancel_event=cancel)
    with pytest.raises(main.TaskCancelled):
        main.generate_timings(opts)


def test_single_flight_double_release_raises() -> None:
    """重複釋放要即刻爆（同 ``threading.Lock`` 一致），唔可以靜默成功。

    靜默成功的話，「多 release 一次」會令閘門在仍有任務跑時被放開 ——
    重複提交防護就失效，而且完全冇徵狀。
    """
    guard = main.SingleFlight()
    assert guard.acquire() is True
    guard.release()
    with pytest.raises(RuntimeError):
        guard.release()


def test_single_flight_locked_property_tracks_state() -> None:
    """``locked`` 只作顯示用，但要同實際狀態同步（UI 靠它決定按鈕文案）。"""
    guard = main.SingleFlight()
    assert guard.locked is False
    guard.acquire()
    assert guard.locked is True
    guard.release()
    assert guard.locked is False
