# -*- coding: utf-8 -*-
"""9.2 第 11 / 12 項 —— 任務取消與重複提交防護。

對應 docs/代碼審查報告.html：
- 9.2-11 任務取消（現況：完全無取消；``ThumbCache.cancelled`` 是死代碼）
- 9.2-12 重複提交相同任務（現況：只有 UI 的 ``_running`` 布林旗標，無獨立可測單元）

⚠️ 全部為 ``xfail(strict=True)`` 契約：實作完成後 XPASS 會令 suite 變紅，
逼開發者回來拆標記 —— 這正是「實作即驗收」的機制。

為何唔測燒錄取消：燒錄屬 ffmpeg 外部程序生命週期（Popen + terminate），
要真影片才測得穩；本檔只鎖「ASR 輪詢可被 cancel_event 中斷」這個
9.2 明文要求的量化契約（< 2 個 poll 週期內拋出）。
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


@pytest.mark.xfail(strict=True, reason="9.2-11 待實作 GenerateOptions.cancel_event（threading.Event）")
def test_generate_options_has_cancel_event(tmp_path: Path) -> None:
    """``GenerateOptions`` 應自帶未觸發的 ``cancel_event``（UI 可無條件 ``.set()``）。"""
    opts = main.GenerateOptions(input=tmp_path / "x.mp4")
    assert isinstance(opts.cancel_event, threading.Event)
    assert opts.cancel_event.is_set() is False


@pytest.mark.xfail(strict=True, reason="9.2-11 待實作 cancel_event 貫穿 ASR 輪詢 + TaskCancelled")
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


@pytest.mark.xfail(strict=True, reason="9.2-11 待實作 main.TaskCancelled（且不可繼承 KaraokeError）")
def test_task_cancelled_is_not_karaoke_error() -> None:
    """取消唔算「錯誤」：``TaskCancelled`` 不應繼承 ``KaraokeError``。

    否則 CLI 入口會把它當失敗並回非零 exit code，令「用戶主動取消」變成「程式出錯」。
    """
    assert not issubclass(main.TaskCancelled, getattr(main, "KaraokeError", Exception))
    assert issubclass(main.TaskCancelled, Exception)


# ---------------------------------------------------------------------------
# 9.2-12 · 重複提交
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="9.2-12 待實作 main.SingleFlight（現只有 UI _running 布林旗標）")
def test_single_flight_blocks_second_acquire() -> None:
    """第二次 ``acquire()`` 必須回 False（= 「已經有任務跑緊」）。"""
    guard = main.SingleFlight()
    assert guard.acquire() is True
    assert guard.acquire() is False
    guard.release()
    assert guard.acquire() is True


@pytest.mark.xfail(strict=True, reason="9.2-12 待實作 main.SingleFlight（現只有 UI _running 布林旗標）")
def test_single_flight_context_manager_releases_on_error() -> None:
    """例外路徑必須自動釋放，否則一次失敗就永久鎖死後續任務。"""
    guard = main.SingleFlight()
    with pytest.raises(RuntimeError):
        with guard:
            raise RuntimeError("模擬任務中途出錯")
    assert guard.acquire() is True, "例外後 guard 未釋放 → 任務永久鎖死"


@pytest.mark.xfail(strict=True, reason="9.2-12 待實作 main.SingleFlight（現只有 UI _running 布林旗標）")
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
