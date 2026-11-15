# -*- coding: utf-8 -*-
"""test_build_release — 交付打包門檻的自檢測試（審查報告 TL-S1）。

覆蓋 build_release.py 最容易靜默回歸的三塊邏輯：

1. allowlist 過濾（禁區資料夾／機密檔名／編譯產物）
2. 機密掃描（命中、截斷、短佔位符唔誤中）
3. 打包前閘門（缺必要檔案、夾帶禁區 → SystemExit）

全部用臨時目錄 + monkeypatch ``build_release.ROOT``，唔會 touch 真 repo，
亦唔會呼叫真的 git（``check_version_bump`` 被 stub 掉）。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import build_release as br  # noqa: E402


def _touch(path: Path, text: str = "x") -> None:
    """建立檔案（連父目錄）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def tree(tmp_path, monkeypatch) -> Path:
    """造一棵最小專案樹，並把 build_release 的常數指向它。"""
    root = tmp_path / "proj"
    root.mkdir()
    _touch(root / "main.py", "print(1)\n")
    _touch(root / "VERSION", "9.9.9\n")
    _touch(root / ".env", "DASHSCOPE_API_KEY=sk-" + "A" * 24)
    _touch(root / "settings.json", "{}")
    _touch(root / "ui" / "__init__.py", "")
    _touch(root / "ui" / "app.py", "x = 1\n")
    _touch(root / "ui" / "__pycache__" / "app.cpython-313.pyc", "bin")
    _touch(root / "_probe" / "shot.png", "bin")
    _touch(root / "docs" / "使用說明.md", "# hi\n")

    monkeypatch.setattr(br, "ROOT", root)
    monkeypatch.setattr(br, "TOP_FILES",
                        ("main.py", "VERSION", ".env", "settings.json"))
    # ui 用 "**/*" 連 __pycache__ 都掃到，順便驗證副檔名過濾
    monkeypatch.setattr(br, "DIR_PATTERNS", {
        "ui": ("**/*",),
        "docs": ("*.md",),
        "_probe": ("**/*",),
    })
    monkeypatch.setattr(br, "REQUIRED_ENTRIES", ("main.py", "VERSION"))
    # 唔跑真 git：版本護欄本身已有 test_version_guard.py 覆蓋
    monkeypatch.setattr(br, "check_version_bump", lambda *a, **k: 0)
    return root


def _rels() -> list[str]:
    """目前 collect() 結果（相對路徑，forward slash）。"""
    return [p.relative_to(br.ROOT).as_posix() for p in br.collect()]


# ---------------------------------------------------------------------------
# 1) allowlist 過濾
# ---------------------------------------------------------------------------

def test_collect_excludes_forbidden_tree(tree: Path) -> None:
    rels = _rels()
    assert ".env" not in rels                     # 機密檔名
    assert "settings.json" not in rels            # 本機設定
    assert not any(r.startswith("_probe/") for r in rels)      # 禁區資料夾
    assert not any("__pycache__" in r for r in rels)           # 編譯目錄
    assert not any(r.endswith(".pyc") for r in rels)           # 編譯產物


def test_collect_keeps_deliverables_and_sorts(tree: Path) -> None:
    rels = _rels()
    for expected in ("main.py", "VERSION", "ui/app.py", "ui/__init__.py",
                     "docs/使用說明.md"):
        assert expected in rels
    assert rels == sorted(rels, key=str.lower)    # 打包結果穩定


# ---------------------------------------------------------------------------
# 2) 機密掃描
# ---------------------------------------------------------------------------

def test_scan_secrets_hits_and_truncates(tree: Path) -> None:
    leak = tree / "leak.py"
    _touch(leak, "KEY = 'sk-" + "B" * 24 + "'\n")
    hits = br.scan_secrets([leak])
    assert len(hits) == 1
    path, frag = hits[0]
    assert path == leak
    assert frag.startswith("sk-") and frag.endswith("…")
    assert len(frag) == 13                        # 12 字元 + 省略號，唔會洩全 key


def test_scan_secrets_ignores_short_placeholder(tree: Path) -> None:
    placeholder = tree / "ok.py"
    _touch(placeholder, "KEY = 'sk-xxxx'\n")
    assert br.scan_secrets([placeholder]) == []


# ---------------------------------------------------------------------------
# 3) 打包前閘門
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tree: Path, tmp_path: Path) -> None:
    out = tmp_path / "dist"
    assert br.build("9.9.9", out, dry_run=True) is None
    assert not out.exists()


def test_missing_required_entry_aborts(tree: Path, tmp_path: Path,
                                      monkeypatch) -> None:
    monkeypatch.setattr(br, "REQUIRED_ENTRIES", ("main.py", "唔存在.py"))
    with pytest.raises(SystemExit):
        br.build("9.9.9", tmp_path / "dist", dry_run=True)


def test_forbidden_entry_aborts(tree: Path, tmp_path: Path, monkeypatch) -> None:
    # 模擬「collect() 過濾失效」：直接餵一個禁區檔案入閘門
    monkeypatch.setattr(br, "REQUIRED_ENTRIES", ())
    monkeypatch.setattr(br, "collect", lambda: [tree / "_probe" / "shot.png"])
    with pytest.raises(SystemExit):
        br.build("9.9.9", tmp_path / "dist", dry_run=True)


def test_build_creates_prefixed_zip(tree: Path, tmp_path: Path) -> None:
    out = tmp_path / "dist"
    zip_path = br.build("9.9.9", out)
    assert zip_path is not None and zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "karaoke-v9.9.9/main.py" in names
    assert "karaoke-v9.9.9/VERSION" in names
    assert "karaoke-v9.9.9/ui/app.py" in names
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


def test_read_version_rejects_empty(tmp_path: Path) -> None:
    version_file = tmp_path / "VERSION"
    version_file.write_text("   ", encoding="utf-8")
    with pytest.raises(SystemExit):
        br.read_version(version_file)
