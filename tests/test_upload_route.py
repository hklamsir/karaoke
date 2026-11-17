# -*- coding: utf-8 -*-
"""ADR-048：上傳只走 SDK（自建 multipart 已退役）。

鎖住：
1. ``_upload_file_with_progress`` 已經不存在（唔可以有人靜靜加返）。
2. SDK 成功 → 一次搞定。
3. SDK 第一次失敗 → 重試一次；成功就唔會跌去 OSS。
4. 兩次都失敗 → 走 OssUtils 後備。
5. SDK 路線**唔會**呼叫 ``progress_cb``（UI 靠跑馬燈交代，唔可以有假百分比）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main


@pytest.fixture()
def wav(tmp_path: Path) -> Path:
    path = tmp_path / "vocals.wav"
    path.write_bytes(b"\x00" * 2048)
    return path


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch) -> None:
    """重試之間唔真等（測試唔應該為 2 秒延遲而變慢）。"""
    monkeypatch.setattr(main, "UPLOAD_RETRY_DELAY_S", 0)


def test_multipart_helper_is_gone() -> None:
    assert not hasattr(main, "_upload_file_with_progress")


def test_sdk_success_on_first_attempt(wav: Path, monkeypatch) -> None:
    calls: list[int] = []

    def fake_sdk(path: Path) -> str:
        calls.append(1)
        return "https://example.invalid/a.wav"

    monkeypatch.setattr(main, "_upload_via_sdk", fake_sdk)
    assert main.upload_audio(wav) == "https://example.invalid/a.wav"
    assert len(calls) == 1


def test_sdk_retries_once_then_succeeds(wav: Path, monkeypatch) -> None:
    calls: list[int] = []

    def flaky(path: Path) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionAbortedError("WinError 10053")
        return "https://example.invalid/ok.wav"

    monkeypatch.setattr(main, "_upload_via_sdk", flaky)
    assert main.upload_audio(wav) == "https://example.invalid/ok.wav"
    assert len(calls) == 2


def test_sdk_failure_falls_back_to_oss(wav: Path, monkeypatch) -> None:
    def always_fail(path: Path) -> str:
        raise ConnectionAbortedError("WinError 10053")

    monkeypatch.setattr(main, "_upload_via_sdk", always_fail)
    monkeypatch.setattr(main, "_upload_via_oss", lambda path: "oss://bucket/key.wav")
    assert main.upload_audio(wav) == "oss://bucket/key.wav"


def test_progress_cb_is_never_called(wav: Path, monkeypatch) -> None:
    """SDK 冇回調點 → 唔可以扮有進度（舊版會報 0–7% 然後卡住）。"""
    seen: list[float] = []
    monkeypatch.setattr(main, "_upload_via_sdk",
                        lambda path: "https://example.invalid/x.wav")

    main.upload_audio(wav, progress_cb=seen.append)
    assert seen == []
