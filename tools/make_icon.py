# -*- coding: utf-8 -*-
"""make_icon — 生成 app 圖示（``assets/app.ico`` + ``assets/app.png``）。

圖示設計：藍底圓角方塊 + 白色咪高風 + 底部卡拉OK進度條（前半 accent 黃），
在 16px 亦認得（只保留最少細節）。改色只要改下面常數再跑：

    .venv-ui\\Scripts\\python.exe tools\\make_icon.py

輸出的 .ico 內含 16/24/32/48/64/128/256 七個尺寸（Windows 會自動揀）。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent.parent / "assets"

BG_TOP = (44, 123, 244)      # 漸變上端
BG_BOTTOM = (17, 76, 174)    # 漸變下端
FG = (255, 255, 255)         # 主體（咪高風）
ACCENT = (255, 210, 74)      # 已唱進度（karaoke 黃）
TRACK = (255, 255, 255, 90)  # 未唱進度（半透明白）

S = 1024                     # 先畫大圖再縮（邊緣乾淨）
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def _rounded(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1),
                                           radius=radius, fill=255)
    return mask


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (size, y)], fill=color)
    return img


def build(size: int = S) -> Image.Image:
    """畫一張尺寸 ``size`` 的圖示（RGBA）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg = _gradient(size, BG_TOP, BG_BOTTOM).convert("RGBA")
    img.paste(bg, (0, 0), _rounded(size, int(size * 0.22)))

    d = ImageDraw.Draw(img)
    u = size / 1024          # 以 1024 為設計基準

    # --- 咪高風主體（上寬下窄的膠囊）---
    d.rounded_rectangle((396 * u, 176 * u, 628 * u, 560 * u),
                        radius=int(116 * u), fill=FG)
    # 收音網格（3 條橫線），細尺寸會自動消失
    for y in (258, 330, 402):
        d.rounded_rectangle((446 * u, (y - 9) * u, 578 * u, (y + 9) * u),
                            radius=int(9 * u), fill=(180, 205, 246))
    # 支架（下方 U 形）+ 支桿 + 底座
    d.arc((296 * u, 372 * u, 728 * u, 700 * u), start=10, end=170,
          fill=FG, width=int(56 * u))
    d.rounded_rectangle((496 * u, 640 * u, 528 * u, 760 * u),
                        radius=int(16 * u), fill=FG)
    d.rounded_rectangle((392 * u, 748 * u, 632 * u, 800 * u),
                        radius=int(26 * u), fill=FG)

    # --- 卡拉OK進度條（底）：前半 accent 黃，後半半透明白 ---
    track_y0, track_y1 = 862 * u, 930 * u
    track_r = int((track_y1 - track_y0) / 2)
    d.rounded_rectangle((152 * u, track_y0, 872 * u, track_y1),
                        radius=track_r, fill=TRACK)
    d.rounded_rectangle((152 * u, track_y0, 560 * u, track_y1),
                        radius=track_r, fill=ACCENT)
    return img


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    big = build(S)
    png_path = OUT_DIR / "app.png"
    big.resize((256, 256), Image.LANCZOS).save(png_path)

    frames = [big.resize((n, n), Image.LANCZOS) for n in ICO_SIZES]
    ico_path = OUT_DIR / "app.ico"
    frames[-1].save(ico_path, format="ICO",
                    sizes=[(n, n) for n in ICO_SIZES],
                    append_images=frames[:-1])
    print(f"written {ico_path} ({ico_path.stat().st_size} bytes)  +  {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
