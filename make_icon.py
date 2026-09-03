# -*- coding: utf-8 -*-
"""生成 GoldPriceTray 应用图标（金币样式，多尺寸 ICO）"""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
         (64, 64), (96, 96), (128, 128), (256, 256)]

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]


def load_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def draw_coin(size):
    S = size * 4  # 4x 超采样后缩放，边缘更平滑
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    m = S * 0.04
    outer = (m, m, S - m, S - m)

    # 外圈深金边
    d.ellipse(outer, fill=(197, 148, 26, 255))
    # 主币面
    r2 = S * 0.07
    d.ellipse((r2, r2, S - r2, S - r2), fill=(247, 197, 62, 255))
    # 高光（左上）
    r3 = S * 0.13
    d.ellipse((r3, r3, S * 0.72, S * 0.72), fill=(255, 214, 102, 255))
    # 内圈细线
    r4 = S * 0.20
    d.ellipse((r4, r4, S - r4, S - r4), outline=(206, 156, 30, 200), width=max(1, S // 64))

    # 币面文字 ¥
    font = load_font(int(S * 0.50))
    text = "¥"
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (S - tw) / 2 - bbox[0]
    y = (S - th) / 2 - bbox[1] - S * 0.02
    # 轻微阴影提升层次
    d.text((x + S * 0.015, y + S * 0.015), text, font=font, fill=(176, 126, 16, 160))
    d.text((x, y), text, font=font, fill=(122, 78, 0, 255))

    return img.resize((size, size), Image.LANCZOS)


def main():
    base = draw_coin(256)
    # save() 的 sizes 参数在各尺寸都由 256 原图重采样，比逐张绘制更一致
    base.save(OUT, format="ICO", sizes=SIZES)
    print("WROTE", OUT, os.path.getsize(OUT), "bytes")
    print("sizes:", ", ".join(f"{w}x{h}" for w, h in SIZES))


if __name__ == "__main__":
    main()
