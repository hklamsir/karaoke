# -*- coding: utf-8 -*-
"""§2.3 / 9.2-11 —— 燒錄取消（``Popen`` + ``terminate`` + 半截檔清理）。

為何要獨立成檔：``test_cancel_and_guard.py`` 只鎖 ASR 輪詢的取消契約
（唔需要 ffmpeg），而燒錄取消是**另一條完全不同的路徑** ——
``subprocess.run`` 換成 ``Popen`` + poll 迴圈。兩者的失效模式唔同：

- ASR 取消失效 → 用戶等足 30 分鐘上限
- 燒錄取消失效 → 用戶**殺唔到** ffmpeg，或者留下一條「燒到一半」的 mp4

最後一點最陰險：輸出檔存在，用戶會以為成功，但字幕只燒到一半。所以本檔
除了驗「有冇拋 TaskCancelled」，更驗「半截檔有冇被刪」。

不變量：
1. ``cancel_event`` 已設 → 拋 ``TaskCancelled``（唔係 ``BurnError``）
2. 半截輸出檔必須刪除
3. 程序真係收到 ``terminate()``（唔係乾等）
4. 冇取消時一切照舊（防「加了取消就所有燒錄都取消」的回歸）
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main


def _fake_ffmpeg_output(cmd: list[str], kwargs: dict) -> Path:
    """由命令 + cwd 還原輸出檔的真實位置（``burn_subtitles`` 可能傳相對路徑）。"""
    out = Path(cmd[-1])
    if not out.is_absolute():
        out = Path(kwargs.get("cwd") or ".") / out
    return out


class _HangingProc:
    """模擬長跑中的 ffmpeg：一直未完，只有 terminate 才結束。"""

    instances: list["_HangingProc"] = []

    def __init__(self, cmd: list[str], **kwargs) -> None:
        self.cmd = cmd
        self.kwargs = kwargs
        self.returncode: int | None = None
        self.terminated = False
        out = _fake_ffmpeg_output(cmd, kwargs)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"partial")          # 燒到一半的半截檔
        _HangingProc.instances.append(self)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 1                  # ffmpeg 被 SIGTERM 後的非零退出碼

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class _FinishingProc:
    """模擬成功的 ffmpeg：第一次 poll 已經有結果，並寫出完整輸出檔。"""

    def __init__(self, cmd: list[str], **kwargs) -> None:
        self.returncode = 0
        out = _fake_ffmpeg_output(cmd, kwargs)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"rendered")

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def terminate(self) -> None:            # pragma: no cover - 正常路徑不會叫
        pass

    def kill(self) -> None:                 # pragma: no cover
        pass


@pytest.fixture
def burn_env(tmp_path: Path):
    """(video, ass, output) —— 最小可燒錄環境（唔需要真影片）。"""
    video = tmp_path / "in.mp4"
    video.write_bytes(b"fake video")
    work = tmp_path / "work"
    work.mkdir()
    ass = work / "a.ass"
    ass.write_text("[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n", encoding="utf-8")
    return video, ass, work / "out.mp4"


def test_burn_cancel_raises_task_cancelled(monkeypatch, burn_env) -> None:
    """取消 → 拋 ``TaskCancelled``（唔可以包成 BurnError，否則 UI 會彈「焼錄失敗」）。"""
    video, ass, out = burn_env
    _HangingProc.instances.clear()
    monkeypatch.setattr(main.subprocess, "Popen", _HangingProc)

    cancel = main.threading.Event()
    cancel.set()

    with pytest.raises(main.TaskCancelled):
        main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac",
                            cancel_event=cancel)

    assert _HangingProc.instances, "冇啟動過 ffmpeg —— 測試前提不成立"


def test_burn_cancel_terminates_ffmpeg(monkeypatch, burn_env) -> None:
    """必須真係叫 ``terminate()``，而唔係「等佢自然完」再若無其事返回。"""
    video, ass, out = burn_env
    _HangingProc.instances.clear()
    monkeypatch.setattr(main.subprocess, "Popen", _HangingProc)

    cancel = main.threading.Event()
    cancel.set()

    with pytest.raises(main.TaskCancelled):
        main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac",
                            cancel_event=cancel)

    assert _HangingProc.instances, "冇啟動過 ffmpeg —— 測試前提不成立"
    assert _HangingProc.instances[0].terminated is True


def test_burn_cancel_removes_partial_output(monkeypatch, burn_env) -> None:
    """半截 mp4 必須刪除。

    這是本檔最重要的一條：檔案存在 = 用戶以為成功，但字幕只燒了前幾分鐘。
    """
    video, ass, out = burn_env
    _HangingProc.instances.clear()
    monkeypatch.setattr(main.subprocess, "Popen", _HangingProc)

    cancel = main.threading.Event()
    cancel.set()

    with pytest.raises(main.TaskCancelled):
        main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac",
                            cancel_event=cancel)

    assert not out.exists(), "取消後留下半截輸出檔 → 用戶會誤以為燒錄成功"


def test_burn_without_cancel_still_succeeds(monkeypatch, burn_env) -> None:
    """冇取消（``cancel_event=None``）→ 走原本阻塞式 ``subprocess.run``，行為不變。"""
    video, ass, out = burn_env

    class _RunResult:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **kwargs):              # noqa: ANN001, ANN003
        target = _fake_ffmpeg_output(cmd, kwargs)
        target.write_bytes(b"rendered")
        return _RunResult()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac")
    assert out.read_bytes() == b"rendered"


def test_burn_with_cancel_event_still_succeeds(monkeypatch, burn_env) -> None:
    """有 ``cancel_event`` 但**未**觸發 → 照樣成功（唔可以一見 Event 就當取消）。"""
    video, ass, out = burn_env
    monkeypatch.setattr(main.subprocess, "Popen", _FinishingProc)

    cancel = main.threading.Event()           # 未 set
    main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac",
                        cancel_event=cancel)
    assert out.read_bytes() == b"rendered"
    assert cancel.is_set() is False


def test_burn_cancel_after_copy_failure_does_not_retry(monkeypatch, burn_env) -> None:
    """copy 音軌失敗後的 aac 重試，同樣要受取消約束（唔可以繞過檢查點）。"""
    video, ass, out = burn_env
    _HangingProc.instances.clear()
    monkeypatch.setattr(main.subprocess, "Popen", _HangingProc)

    cancel = main.threading.Event()
    cancel.set()

    with pytest.raises(main.TaskCancelled):
        main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "flac",
                            cancel_event=cancel)

    assert len(_HangingProc.instances) == 1, "取消後仍然重試 = 檢查點被繞過"


def test_task_cancelled_not_wrapped_into_burn_error(monkeypatch, burn_env) -> None:
    """``TaskCancelled`` 唔可以被 ``except Exception`` 之類吞掉或轉型。"""
    video, ass, out = burn_env
    monkeypatch.setattr(main.subprocess, "Popen", _HangingProc)
    cancel = main.threading.Event()
    cancel.set()

    try:
        main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac",
                            cancel_event=cancel)
    except main.TaskCancelled:
        pass
    except main.BurnError as exc:              # pragma: no cover - 失敗時才走
        pytest.fail(f"取消被包成 BurnError：{exc}")
    else:                                      # pragma: no cover
        pytest.fail("取消冇拋任何例外")


def test_cancelled_output_unlink_is_best_effort(monkeypatch, burn_env) -> None:
    """輸出檔刪不到（例如被播放器鎖住）時，唔可以蓋掉原本的 TaskCancelled。"""
    video, ass, out = burn_env
    monkeypatch.setattr(main.subprocess, "Popen", _HangingProc)

    real_unlink = Path.unlink

    def locked_unlink(self, *args, **kwargs):  # noqa: ANN001, ANN003
        if self.name == "out.mp4":
            raise PermissionError("檔案被其他程式鎖住")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    cancel = main.threading.Event()
    cancel.set()

    # 刪除失敗只記錄警告；TaskCancelled 必須照拋（否則 UI 會顯示「燒錄失敗」）
    with pytest.raises(main.TaskCancelled):
        main.burn_subtitles("/fake/ffmpeg", video, ass, out, 1920, 1080, "aac",
                            cancel_event=cancel)
