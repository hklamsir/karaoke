# -*- coding: utf-8 -*-
"""test_split_and_space — 字級斷句（#1）＋ 半形空格保留（#2）＋ 預覽字級單位（#3）。

用戶 2026-11-11 三個回報：
1. 「喺「逝」字按斷句，「去」仍然同「逝」同一行」→ 舊版只可以喺 WordGroup
   邊界斷，「逝去」係**一個**詞所以永遠切唔開
2. 「歌詞有半形空格（種種 後面），畫面冇顯示」→ ``display_text`` 用
   ``re.sub(r"\\s+", "")`` 全刪，但 ``chars[]`` 仍然有 ``" "`` →
   UI 同燒錄唔一致
3. 「1280×720 / 字級 96，預覽同燒錄字大小唔同」→ Tk 正數 size 係**點**，
   libass ``Fontsize`` 係**像素**（實測 96pt → 128px，大 4/3）

兩種跑法：pytest，或直跑 ``.venv-ui/Scripts/python.exe tests/test_split_and_space.py``
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

from ui import commands, models  # noqa: E402
from ui.state import EditorState  # noqa: E402

PASS: list[str] = []


def check(name: str, cond: bool) -> None:
    """記錄一條斷言結果；失敗即拋。"""
    PASS.append(name)
    print(("✅ " if cond else "❌ ") + name)
    assert cond, name


def _word(text: str, begin: int, spans: list[tuple[str, int, int]],
          punctuation: str = "", estimated: bool = True) -> dict:
    """由 ``(字, begin, end)`` 造一個 WordGroup。"""
    return {
        "text": text, "begin_ms": begin, "end_ms": spans[-1][2],
        "estimated": estimated, "punctuation": punctuation,
        "chars": [{"text": c, "begin_ms": b, "end_ms": e} for c, b, e in spans],
    }


def _one_line_doc(words: list[dict]) -> dict:
    """只有一行的 doc（時間由 words 推）。"""
    return {
        "schema_version": 3,
        "source": {"hold_ms": 300},
        "style": {},
        "lines": [{
            "start_ms": words[0]["begin_ms"], "end_ms": words[-1]["end_ms"],
            "text": "".join(w["text"] for w in words),
            "break_reason": "sentence", "words": words,
        }],
    }


def _char_spans(line: dict) -> list[tuple[str, int, int]]:
    return [(c["text"], c["begin_ms"], c["end_ms"])
            for w in line["words"] for c in w["chars"]]


# ---------------------------------------------------------------------------
# #1 · 字級斷句（切穿一個詞，時間軸無損）
# ---------------------------------------------------------------------------

def test_split_inside_word() -> None:
    """「逝去」= 一個 2 字詞 → 喺「逝」之後斷要開得成兩行。"""
    st = EditorState()
    st.doc = _one_line_doc([_word("逝去", 19060,
                                 [("逝", 19060, 19420), ("去", 19420, 19780)])])
    before = _char_spans(st.doc["lines"][0])

    st.push(commands.SplitLine(0, 0, 0))          # 第 0 個詞、第 0 個字之後
    check("#1: 斷句後有兩行", len(st.doc["lines"]) == 2)
    check("#1: 上行 = 逝", st.doc["lines"][0]["text"] == "逝")
    check("#1: 下行 = 去", st.doc["lines"][1]["text"] == "去")
    check("#1: 下行 break_reason = manual",
          st.doc["lines"][1]["break_reason"] == "manual")
    check("#1: 下行 start = 去 begin_ms", int(st.doc["lines"][1]["start_ms"]) == 19420)
    check("#1: 上行 end = 逝 end_ms", int(st.doc["lines"][0]["end_ms"]) == 19420)
    check("#1: 每個詞只剩一個 char",
          all(len(w["chars"]) == 1 for l in st.doc["lines"] for w in l["words"]))

    merged = _char_spans(st.doc["lines"][0]) + _char_spans(st.doc["lines"][1])
    check("#1: char 時間軸完全無損", merged == before)

    st.undo()
    check("#1: undo 還原成一行", len(st.doc["lines"]) == 1)
    check("#1: undo 後詞數還原", len(st.doc["lines"][0]["words"]) == 1)
    check("#1: undo 後 char 時間軸還原", _char_spans(st.doc["lines"][0]) == before)


def test_split_inside_word_middle() -> None:
    """三字詞中間斷（「輪迴塵」斷喺「迴」之後）→ 兩行 = 輪迴 / 塵。"""
    st = EditorState()
    st.doc = _one_line_doc([_word("輪迴塵", 1000, [
        ("輪", 1000, 2000), ("迴", 2000, 3000), ("塵", 3000, 4000)])])
    st.push(commands.SplitLine(0, 0, 1))
    check("#1: 三字詞中間斷 → 兩行", len(st.doc["lines"]) == 2)
    check("#1: 上行 = 輪迴", st.doc["lines"][0]["text"] == "輪迴")
    check("#1: 下行 = 塵", st.doc["lines"][1]["text"] == "塵")


def test_split_keeps_trailing_punctuation() -> None:
    """詞尾標點要跟最後一個字（唔可以留喺上行）。"""
    st = EditorState()
    st.doc = _one_line_doc([_word("逝去。", 1000, [
        ("逝", 1000, 1500), ("去", 1500, 2000)], punctuation="。")])
    st.push(commands.SplitLine(0, 0, 0))
    check("#1: 標點唔會留喺上行",
          not (st.doc["lines"][0]["words"][0].get("punctuation") or ""))
    check("#1: 標點跟去下行",
          st.doc["lines"][1]["words"][0].get("punctuation") == "。")


def test_split_at_word_end_still_works() -> None:
    """舊語意（char_i=None）= 喺第 word_i 個詞之前斷，唔可以回歸。"""
    st = EditorState()
    st.doc = _one_line_doc([
        _word("逝", 1000, [("逝", 1000, 1500)]),
        _word("去", 1500, [("去", 1500, 2000)]),
    ])
    st.push(commands.SplitLine(0, 1))
    check("#1: 舊語意仍然斷得開", len(st.doc["lines"]) == 2)
    check("#1: 舊語意上行 = 逝", st.doc["lines"][0]["text"] == "逝")


def test_split_whole_last_word_is_noop() -> None:
    """最後一個詞嘅最後一個字：冇嘢可斷（UI 會先彈提示，models 回 None）。"""
    line = _one_line_doc([_word("逝去", 1000, [
        ("逝", 1000, 1500), ("去", 1500, 2000)])])["lines"][0]
    check("#1: 最後一個字之後 → None",
          models.split_line_at_char(line, 0, 1) is None)


# ---------------------------------------------------------------------------
# #2 · 半形空格保留（UI / chars / 燒錄三邊一致）
# ---------------------------------------------------------------------------

def test_display_text_keeps_one_space() -> None:
    """「種種 昨日」入面嘅半形空格要保住（只折疊、唔刪）。"""
    line = {"words": [_word("種種 昨日", 0, [
        ("種", 0, 100), ("種", 100, 200), (" ", 200, 201),
        ("昨", 201, 300), ("日", 300, 400)])]}
    check("#2: 保留一個半形空格", models.display_text(line) == "種種 昨日")


def test_display_text_collapses_and_strips() -> None:
    """連續空白折疊成一個；頭尾空白清走（唔會出「幽靈空格」）。"""
    inner = {"words": [_word("種種   昨日", 0, [
        ("種", 0, 100), (" ", 100, 101)])]}
    check("#2: 詞內連續空白折疊成一個", models.display_text(inner) == "種種 昨日")

    edges = {"words": [_word("  種種昨日  ", 0, [
        ("種", 0, 100), (" ", 100, 101)])]}
    check("#2: 頭尾空白清走", models.display_text(edges) == "種種昨日")


def test_punctuation_space_not_doubled() -> None:
    """標點變空格嘅規則遇到已有空白嘅詞，唔可以變兩個空格。"""
    line = {"words": [
        _word("種種 ", 0, [("種", 0, 100), (" ", 100, 101)], punctuation="，"),
        _word("昨日", 101, [("昨", 101, 200), ("日", 200, 300)]),
    ]}
    check("#2: 唔會出雙空格", models.display_text(line) == "種種 昨日")


def test_set_line_text_keeps_space_char() -> None:
    """改歌詞打空格 → chars 要有 ``" "`` 呢個 char（燒錄同預覽都見到罅）。"""
    st = EditorState()
    st.doc = _one_line_doc([_word("種種昨日", 0, [
        ("種", 0, 100), ("種", 100, 200), ("昨", 200, 300), ("日", 300, 400)])])
    st.push(commands.SetLineText(0, "種種 昨日"))
    line = st.doc["lines"][0]
    check("#2: 行文字有空格", line["text"] == "種種 昨日")
    check("#2: chars 有真空格 char",
          any(c["text"] == " " for c in line["words"][0]["chars"]))
    check("#2: 空格有時間（唔會 0 長度漂移）",
          all(int(c["end_ms"]) > int(c["begin_ms"]) for c in line["words"][0]["chars"]))
    # 存檔前的 normalize 亦唔可以食咗個空格
    models.normalize(st.doc)
    check("#2: normalize 之後仍然有空格", st.doc["lines"][0]["text"] == "種種 昨日")


def test_space_survives_save_and_reload(tmp_path) -> None:
    """寫入 timings.json 再讀返，空格仍然在（round-trip）。"""
    st = EditorState()
    st.doc = _one_line_doc([_word("種種昨日", 0, [
        ("種", 0, 100), ("種", 100, 200), ("昨", 200, 300), ("日", 300, 400)])])
    st.push(commands.SetLineText(0, "種種 昨日"))
    path = tmp_path / "t.json"
    st.path = path
    st.save()
    again = models.load_doc(path)
    check("#2: 存檔再讀返仍然有空格",
          again["lines"][0]["text"] == "種種 昨日")


def test_materialize_punct_space() -> None:
    """「詞尾標點 → 半形空格」要實體化成真 char，令樹／微調／預覽三邊一致。

    用戶 2026-11-11 回報：歌詞樹「逝去種種 昨日經已死」有空格，但逐字微調同
    預覽都冇 —— 因為個空格只係 display_text 即場推導，chars[] 冇呢個字。
    """
    line = {"words": [
        _word("種種", 0, [("種", 0, 500), ("種", 500, 1000)], punctuation="，"),
        _word("昨日", 1000, [("昨", 1000, 1500), ("日", 1500, 2000)]),
    ]}
    check("#4: 實體化前 chars 冇空格",
          [c["text"] for c in line["words"][1]["chars"]] == ["昨", "日"])
    check("#4: 實體化前 display_text 有空格", models.display_text(line) == "種種 昨日")

    doc = {"lines": [line]}
    inserted = models.materialize_punct_spaces(doc)
    check("#4: 插入一個空格 char", inserted == 1)
    check("#4: chars 有空格",
          [c["text"] for c in line["words"][1]["chars"]] == [" ", "昨", "日"])
    check("#4: display_text 唔會變兩個空格", models.display_text(line) == "種種 昨日")
    check("#4: 再跑一次唔會再加", models.materialize_punct_spaces(doc) == 0)


def test_materialize_space_single_in_ass() -> None:
    """實體化之後，ASS 都只可以有一個空格（唔可以同標點規則疊加）。"""
    import main

    line = {"start_ms": 0, "end_ms": 3000, "text": "", "break_reason": "",
            "words": [
                _word("種種", 0, [("種", 0, 500), ("種", 500, 1000)], punctuation="，"),
                _word("昨日", 1000, [("昨", 1000, 1500), ("日", 1500, 2000)]),
            ]}
    doc = {
        "schema_version": 3, "source": {"hold_ms": 300},
        "style": {"play_res_x": 1280, "play_res_y": 720, "font_name": "Microsoft JhengHei",
                  "font_size": 48, "primary_color": "#FFFF00", "secondary_color": "#FFFFFF",
                  "outline_color": "#000000", "alignment": 2, "margin_v": 40,
                  "outline_width": 3.0, "lead_in_ms": 0, "countdown_dots": False},
        "lines": [line],
    }
    models.materialize_punct_spaces(doc)
    lines, style = main.timings_doc_to_lines(doc)
    ass = main.build_karaoke_ass(lines, style)
    dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][-1]
    body = dialogue.split(",,", 1)[-1]
    check("#4: ASS 只有一個空格", body.count(" ") == 1)


def test_materialize_punct_space() -> None:
    """「詞尾標點 → 半形空格」要實體化成真 char，令樹／微調／預覽三邊一致。

    用戶 2026-11-11 回報：歌詞樹「逝去種種 昨日經已死」有空格，但逐字微調同
    預覽都冇 —— 因為個空格只係 display_text 即場推導，chars[] 冇呢個字。
    """
    line = {"words": [
        _word("種種", 0, [("種", 0, 500), ("種", 500, 1000)], punctuation="，"),
        _word("昨日", 1000, [("昨", 1000, 1500), ("日", 1500, 2000)]),
    ]}
    check("#4: 實體化前 chars 冇空格",
          [c["text"] for c in line["words"][1]["chars"]] == ["昨", "日"])
    check("#4: 實體化前 display_text 有空格", models.display_text(line) == "種種 昨日")

    doc = {"lines": [line]}
    inserted = models.materialize_punct_spaces(doc)
    check("#4: 插入一個空格 char", inserted == 1)
    check("#4: chars 有空格",
          [c["text"] for c in line["words"][1]["chars"]] == [" ", "昨", "日"])
    check("#4: display_text 唔會變兩個空格", models.display_text(line) == "種種 昨日")
    check("#4: 再跑一次唔會再加", models.materialize_punct_spaces(doc) == 0)


def test_materialize_space_single_in_ass() -> None:
    """實體化之後，ASS 都只可以有一個空格（唔可以同標點規則疊加）。"""
    import main

    line = {"start_ms": 0, "end_ms": 3000, "text": "", "break_reason": "",
            "words": [
                _word("種種", 0, [("種", 0, 500), ("種", 500, 1000)], punctuation="，"),
                _word("昨日", 1000, [("昨", 1000, 1500), ("日", 1500, 2000)]),
            ]}
    doc = {
        "schema_version": 3, "source": {"hold_ms": 300},
        "style": {"play_res_x": 1280, "play_res_y": 720, "font_name": "Microsoft JhengHei",
                  "font_size": 48, "primary_color": "#FFFF00", "secondary_color": "#FFFFFF",
                  "outline_color": "#000000", "alignment": 2, "margin_v": 40,
                  "outline_width": 3.0, "lead_in_ms": 0, "countdown_dots": False},
        "lines": [line],
    }
    models.materialize_punct_spaces(doc)
    lines, style = main.timings_doc_to_lines(doc)
    ass = main.build_karaoke_ass(lines, style)
    dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")][-1]
    body = dialogue.split(",,", 1)[-1]
    check("#4: ASS 只有一個空格", body.count(" ") == 1)


# ---------------------------------------------------------------------------
# #3 · 預覽字級 = 像素（同 libass Fontsize 對齊）
# ---------------------------------------------------------------------------

def test_pixel_font_uses_pixel_units(monkeypatch) -> None:
    """``pixel_font`` 一定要傳**負** size 落 ``tkfont.Font``。

    Tk 語意：size 正數 = 點（96 → 128px @96dpi）、負數 = 像素；
    libass ``Fontsize`` 係 PlayRes 像素 → 一定要負數，否則預覽大 4/3。

    ⚠️ 用 monkeypatch 代替真 Tk：同一 process 開／關多個 Tk root 會令
    ``test_time_spinbox`` 的 ttkbootstrap 單例炸掉（實測 TclError）。
    真機實測：``Font(size=96).measure("種") == 128``、``size=-96 → 96``。
    """
    from ui.widgets import overlay

    captured: dict = {}

    class _FakeFont:
        """假 Font：只負責記錄 size、回傳「像素」寬度。"""

        def __init__(self, root=None, family=None, size=None) -> None:
            captured.update(root=root, family=family, size=size)

        def measure(self, ch: str) -> int:
            return abs(int(captured["size"]))

    monkeypatch.setattr(overlay.tkfont, "Font", _FakeFont)
    overlay._FONT_CACHE.clear()
    try:
        font = overlay.pixel_font("Microsoft JhengHei", 96)
        check(f"#3: 傳落 Tk 的 size = {captured['size']}（要負數 = 像素）",
              captured["size"] == -96)
        check("#3: 96px 字型量得 96px（唔係 128px）", font.measure("種") == 96)
        # 同一個 px 會重用 cache（播放時 33fps 重繪，唔可以每次都開新 Font）
        check("#3: 字型有 cache", overlay.pixel_font("Microsoft JhengHei", 96) is font)
    finally:
        overlay._FONT_CACHE.clear()


def test_overlay_size_matches_burn_ratio() -> None:
    """``_metrics`` 的 size 必須 = font_size × (畫布高 / PlayResY)（像素比例）。"""
    from ui.widgets.overlay import _metrics

    style = {"play_res_y": 720, "font_size": 96, "margin_v": 40, "outline_width": 3}
    size, _outline, _mv, _gap = _metrics(style, 360)
    check(f"#3: 360px 畫布 / 720 PlayRes → 字級 {size}px（期望 48）", size == 48)


# ---------------------------------------------------------------------------
# 直跑入口
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        test_split_inside_word,
        test_split_inside_word_middle,
        test_split_keeps_trailing_punctuation,
        test_split_at_word_end_still_works,
        test_split_whole_last_word_is_noop,
        test_display_text_keeps_one_space,
        test_display_text_collapses_and_strips,
        test_punctuation_space_not_doubled,
        test_set_line_text_keeps_space_char,
    ]
    failed: list[tuple[str, str]] = []
    for case in cases:
        try:
            case()
        except AssertionError as exc:
            failed.append((case.__name__, str(exc)))
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(cases)}")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        return 1
    print(f"ALL PASS ({len(PASS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
