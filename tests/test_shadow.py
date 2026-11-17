# -*- coding: utf-8 -*-
"""ADR-049 / ADR-050：描邊自訂 px 與字幕陰影（ASS ``Shadow`` / ``BackColour``）。

鎖住三件事：
1. 陰影關閉時 ASS 輸出同舊版**一模一樣**（``&H80000000`` + ``Shadow=0``）——
   舊 timings.json 重燒唔可以變色。
2. 開啟後 ``Shadow`` 與 ``BackColour`` 的換算（含 alpha 反轉）。
3. timings.json 只在啟用時才寫陰影欄位，讀回時缺欄位等於關閉。
"""

from __future__ import annotations

import pytest

import main


def _style_fields(config: main.StyleConfig) -> list[str]:
    """回傳 ASS ``Style:`` 行的欄位（去掉 ``Style: Karaoke,`` 前綴）。"""
    ass = main.build_karaoke_ass([], config)
    line = next(item for item in ass.splitlines() if item.startswith("Style:"))
    return line.split(",", 2)[2].split(",")


# 欄位索引（見 Format 行）
IDX_BACK_COLOUR = 4       # 0=Fontsize 之後：3=OutlineColour → 4=BackColour
IDX_BORDER_STYLE = 13
IDX_OUTLINE = 14
IDX_SHADOW = 15


def test_default_style_is_byte_identical_to_pre_adr050() -> None:
    """陰影預設關閉 → BackColour 仍係舊版寫死的 &H80000000、Shadow 仍係 0。"""
    fields = _style_fields(main.StyleConfig())
    assert fields[IDX_BACK_COLOUR] == "&H80000000"
    assert fields[IDX_BORDER_STYLE] == "1"
    assert fields[IDX_SHADOW] == "0"


def test_shadow_writes_offset_and_back_colour() -> None:
    """3px 陰影 + #204060 + 80% 不透明 → Shadow=3、BackColour alpha=0x33。"""
    fields = _style_fields(main.StyleConfig(
        shadow_width=3.0, shadow_color="#204060", shadow_opacity=80))
    assert fields[IDX_SHADOW] == "3"
    assert fields[IDX_BACK_COLOUR] == "&H33604020"


@pytest.mark.parametrize(
    ("color", "opacity", "expected"),
    [
        ("#000000", 50, "&H80000000"),     # 50% 透明黑 = 舊版寫死值
        ("#FFFFFF", 100, "&H00FFFFFF"),    # 全不透明：alpha=00
        ("#FFFFFF", 0, "&HFFFFFFFF"),      # 全透明：alpha=FF
        ("#3366CC", 0, "&HFFCC6633"),      # BGR 反序 + 全透明
        ("#000000", 150, "&H00000000"),    # 超範圍夾回 100
        ("#000000", -5, "&HFF000000"),     # 負數夾回 0
    ],
)
def test_hex_to_ass_color_alpha(color: str, opacity: int, expected: str) -> None:
    assert main.hex_to_ass_color_alpha(color, opacity) == expected


def test_hex_to_ass_color_alpha_rejects_bad_colour() -> None:
    with pytest.raises(ValueError):
        main.hex_to_ass_color_alpha("#ZZZZZZ", 50)


def test_shadow_only_written_when_enabled() -> None:
    """陰影關閉 → 三個欄位都不入 timings.json（舊檔 byte-identical 的關鍵）。"""
    doc = main.lines_to_timings_doc([], main.StyleConfig(), meta={"source": "t"})
    style = doc["style"]
    assert "shadow_width" not in style
    assert "shadow_color" not in style
    assert "shadow_opacity" not in style


def test_shadow_round_trip_through_timings() -> None:
    origin = main.StyleConfig(shadow_width=2.5, shadow_color="#101010",
                              shadow_opacity=70)
    doc = main.lines_to_timings_doc([], origin, meta={"source": "t"})
    assert doc["style"]["shadow_width"] == 2.5
    assert doc["style"]["shadow_color"] == "#101010"
    assert doc["style"]["shadow_opacity"] == 70

    _lines, style = main.timings_doc_to_lines(doc)
    assert (style.shadow_width, style.shadow_color, style.shadow_opacity) == \
        (2.5, "#101010", 70)


def test_default_shadow_colour_not_written_to_keep_file_clean() -> None:
    """只改 px、顏色與透明度都用預設 → 只寫 shadow_width 一個欄位。"""
    doc = main.lines_to_timings_doc(
        [], main.StyleConfig(shadow_width=4.0), meta={"source": "t"})
    style = doc["style"]
    assert style["shadow_width"] == 4.0
    assert "shadow_color" not in style
    assert "shadow_opacity" not in style


def test_old_document_without_shadow_keys_loads_as_off() -> None:
    """舊 timings.json 冇陰影欄位 → 當關閉，燒錄行為同以前一樣。"""
    doc: dict = {"style": {"font_size": 60}, "lines": []}
    _lines, style = main.timings_doc_to_lines(doc)
    assert style.shadow_width == 0.0
    assert style.shadow_color == "#000000"
    assert style.shadow_opacity == 50


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 50), ("", 50), ("abc", 50), (150, 100), (-3, 0), ("60", 60)],
)
def test_safe_opacity_clamps(raw: object, expected: int) -> None:
    assert main._safe_opacity(raw) == expected


def test_blend_over_approximates_semi_transparent_black() -> None:
    """50% 黑疊在影片底色 #0E0E12 上 ≈ #070709（Tk 冇 alpha 的近似值）。"""
    from ui.widgets.overlay import blend_over

    assert blend_over("#000000", "#0E0E12", 50) == "#070709"
    assert blend_over("#FFFFFF", "#0E0E12", 100) == "#FFFFFF"
    assert blend_over("#FFFFFF", "#0E0E12", 0) == "#0E0E12"
    # 壞色碼唔應該拋（預覽唔可以因為打錯 hex 而死）
    assert blend_over("#ZZZ", "#0E0E12", 50) == "#0E0E12"
