# -*- coding: utf-8 -*-
"""例外階層契約（ADR-024）—— 鎖住「例外化之後 CLI 退出碼一字不變」。

這批測試的價值不在於驗證某個 bug，而是**防止未來的重構偷偷改掉對外契約**：

- `code` 是給 CI 腳本 / 捷徑 / 同事 .bat 看的。以前是 `sys.exit(3)`，現在是
  `raise AudioExtractionError`，但退出碼必須仍然是 3 —— 否則自動化流程會靜默
  變成「永遠成功」或「永遠失敗」。
- 訊息長度也要鎖：例外訊息會直接顯示在 UI 對話框，塞 2500 字 stderr 會爆窗。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import main


# ---------------------------------------------------------------------------
# 1 · 類別與退出碼
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("KaraokeError", 1),
        ("UsageError", 2),
        ("ToolMissingError", 2),
        ("AudioExtractionError", 3),
        ("AsrError", 4),
        ("NoSubtitlesError", 4),
        ("BurnError", 5),
    ],
)
def test_cli_exit_code_contract(name: str, code: int) -> None:
    """每個子類的 ``code`` 必須等於重構前對應的 ``sys.exit(N)``。"""
    assert getattr(main, name).code == code


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        ("UsageError", "KaraokeError"),
        ("ToolMissingError", "UsageError"),
        ("AudioExtractionError", "KaraokeError"),
        ("AsrError", "KaraokeError"),
        ("NoSubtitlesError", "AsrError"),
        ("BurnError", "KaraokeError"),
    ],
)
def test_inheritance_contract(child: str, parent: str) -> None:
    """繼承關係即 catch 的粒度契約；改動會影響所有 `except` 分支。"""
    assert issubclass(getattr(main, child), getattr(main, parent))


def test_all_are_exceptions_not_system_exit() -> None:
    """全部必須是 ``Exception`` 子類（**不是** ``SystemExit``）。

    這是本 ADR 的核心：UI 背景 thread 用 ``except Exception`` 就能接住，
    唔再需要 ``except SystemExit`` 這種繞路寫法。
    """
    for name in ("KaraokeError", "UsageError", "ToolMissingError",
                 "AudioExtractionError", "AsrError", "NoSubtitlesError", "BurnError"):
        cls = getattr(main, name)
        assert issubclass(cls, Exception), name
        assert not issubclass(cls, SystemExit), name


def test_code_can_be_overridden_per_instance() -> None:
    """少數無法歸類的情況可用 ``code=`` 覆寫，而唔需要為它開一個新子類。"""
    exc = main.KaraokeError("某個未分類錯誤", code=7)
    assert exc.code == 7
    assert str(exc) == "某個未分類錯誤"
    # 唔會污染類別屬性
    assert main.KaraokeError.code == 1


# ---------------------------------------------------------------------------
# 2 · CLI 端到端：退出碼真的沒變
# ---------------------------------------------------------------------------


def test_cli_generate_missing_input_returns_2(tmp_path: Path) -> None:
    """``generate`` 輸入不存在 → 退出碼 2（以前是 ``sys.exit(2)``）。

    刻意用 ``main.main([...])`` 而非 subprocess：快、無需另開進程，
    而且直接鎖住「例外 → 退出碼」的轉換層。
    """
    rc = main.main(["generate", "-i", str(tmp_path / "nope.mp4"), "--api-key", "sk-test"])
    assert rc == 2


def test_cli_burn_missing_input_returns_2(tmp_path: Path) -> None:
    rc = main.main(["burn", "-i", str(tmp_path / "nope.mp4"),
                    "-s", str(tmp_path / "nope.ass")])
    assert rc == 2


def test_cli_burn_missing_ass_returns_2(tmp_path: Path) -> None:
    """影片存在但字幕不存在 → 也是 2（兩個前置檢查都要保留）。"""
    video = tmp_path / "in.mp4"
    video.write_bytes(b"\x00" * 32)
    rc = main.main(["burn", "-i", str(video), "-s", str(tmp_path / "nope.ass")])
    assert rc == 2


def test_cli_render_missing_timings_returns_2(tmp_path: Path) -> None:
    rc = main.main(["render", str(tmp_path / "nope_timings.json")])
    assert rc == 2


def test_cli_generate_rejects_bad_color(tmp_path: Path) -> None:
    """色碼字元非法 → 退出碼 2，唔可以出 traceback（§2.10 的 CLI 面）。"""
    video = tmp_path / "mv.mp4"
    video.write_bytes(b"\x00" * 32)
    rc = main.main(["generate", "-i", str(video), "--api-key", "sk-test",
                    "--primary-color", "yellow"])
    assert rc == 2


def test_cli_render_rejects_bad_color(tmp_path: Path, doc) -> None:
    """render 的顏色覆寫亦要先驗 —— 否則 --sync-style 會把壞色碼寫回 JSON。"""
    import json

    src = tmp_path / "t.json"
    src.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rc = main.main(["render", str(src), "--primary-color", "#ZZZZZZ"])
    assert rc == 2


def test_cli_error_paths_always_log(tmp_path: Path, doc, caplog) -> None:
    """**不變量**：CLI 失敗必須有 ERROR 日誌，唔可以靜默只剩退出碼。

    `main()` 只負責把例外轉退出碼、刻意不重複輸出（否則同一錯誤印兩次），
    代價是「拋出點忘記 LOGGER.error」時用戶會完全唔知發生咩事 ——
    這條測試就是防這個。
    """
    import json
    import logging

    src = tmp_path / "t.json"
    src.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    scenarios = [
        (["generate", "-i", str(tmp_path / "nope.mp4"), "--api-key", "sk-test"], "缺輸入檔"),
        (["render", str(tmp_path / "nope.json")], "缺 timings"),
        (["render", str(src), "--primary-color", "#ZZZZZZ"], "壞色碼"),
    ]
    for argv, label in scenarios:
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="karaoke"):
            rc = main.main(argv)
        assert rc != 0, label
        assert any(r.levelno >= logging.ERROR for r in caplog.records), \
            f"{label}：失敗但完全冇 ERROR 日誌 —— 用戶只會見到退出碼 {rc}"


def test_cli_success_returns_0(tmp_path: Path, doc) -> None:
    """正常路徑仍然是 0 —— 防止「全部變例外」之後連成功都被當成失敗。"""
    import json

    src = tmp_path / "t.json"
    src.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "t.ass"
    rc = main.main(["render", str(src), "-o", str(out)])
    assert rc == 0
    assert out.is_file()


# ---------------------------------------------------------------------------
# 3 · 訊息必須短（UI 直接顯示，太長會爆窗）
# ---------------------------------------------------------------------------


def test_error_message_is_single_line(tmp_path: Path, monkeypatch) -> None:
    """抽音失敗的訊息必須單行且簡短 —— 詳細 stderr 留在 log，不進對話框。"""
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"\x00" * 8)

    def fake_run(*args, **kwargs):              # noqa: ANN002, ANN003
        from types import SimpleNamespace
        return SimpleNamespace(returncode=1, stderr="x" * 3000)

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    with pytest.raises(main.AudioExtractionError) as info:
        main.extract_temp_audio("ffmpeg", video, tmp_path)

    message = str(info.value)
    assert "\n" not in message, "訊息含換行 → UI 對話框會爆"
    assert len(message) <= 120, f"訊息太長（{len(message)} 字），詳細內容應留在 log"
