# -*- coding: utf-8 -*-
"""小团子表情包生成器（v6）
生成扁平可爱猫咪表情（512×512 JPG，中文短句），输出到 ./sticker_pack/。
emotion 值域以引擎为准：happy/hungry/sleep/angry/confused/sad/proud/neutral
命名 <emotion>_<n>.jpg，n 从 2 起（与 pics/ 里已有的 _1 衔接）。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sticker_pack")
W = H = 512
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def load_font(size):
    for fp in FONT_CANDIDATES:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


F_BIG = load_font(58)
F_MID = load_font(44)
F_SM = load_font(30)

OUTLINE = "#E2A276"
FUR = "#F9DFC8"
FUR2 = "#F3CBA9"
INNER_EAR = "#F6C1A0"
DARK = "#5A4636"

# 每个 emotion：背景色板、短句池、额外装饰
EMO = {
    "happy":   {"bg": ["#FFF3D6", "#FFE9C2", "#FFEFD0"],
                "cap": ["哈哈哈哈", "好耶好耶喵", "本喵最开心", "尾巴摇摇"],
                "accent": "♪"},
    "hungry":  {"bg": ["#FFEFE2", "#FFE6D2", "#FFF0DA"],
                "cap": ["饿饿", "小鱼干呢？", "肚肚咕咕叫", "想吃想吃"],
                "accent": ""},
    "sleep":   {"bg": ["#E9E6F5", "#E2DEF2", "#EEEBF7"],
                "cap": ["困死了喵", "呼呼…", "要睡了喵", "眼皮打架"],
                "accent": "Z"},
    "angry":   {"bg": ["#FFE3DE", "#FFDAD3", "#FFE7E1"],
                "cap": ["气鼓鼓", "哼！", "炸毛了喵", "不理你了"],
                "accent": "!"},
    "confused": {"bg": ["#E7F0FF", "#DEEAFF", "#EAF2FF"],
                "cap": ["嗯？？", "啥啥啥", "没看懂喵", "歪头"],
                "accent": "?"},
    "sad":     {"bg": ["#E6EEF7", "#DDE8F4", "#EAF1F9"],
                "cap": ["呜…", "委屈巴巴", "耷耳朵", "抱抱喵"],
                "accent": "-"},
    "proud":   {"bg": ["#FFEFC9", "#FFE7B8", "#FFF2D2"],
                "cap": ["本喵厉害吧", "哼哼～", "小场面喵", "夸夸我"],
                "accent": "★"},
    "neutral": {"bg": ["#EEF1E9", "#E6EBE0", "#F1F4EC"],
                "cap": ["盯——", "本喵在呢", "安静蹲坐", "看看你"],
                "accent": ""},
}

ACCENT_COLORS = {"happy": "#E8A020", "sleep": "#8E7CC3", "angry": "#E05A4A",
                 "confused": "#4A86E8", "sad": "#5B8FC7", "proud": "#D9A020"}


def draw_cat(d, emo, idx):
    cx, cy = 256, 250
    rx, ry = 152, 138
    # 耳朵
    d.polygon([(108, 168), (94, 62), (196, 132)], fill=FUR, outline=OUTLINE)
    d.polygon([(404, 168), (418, 62), (316, 132)], fill=FUR, outline=OUTLINE)
    d.polygon([(122, 156), (114, 92), (176, 138)], fill=INNER_EAR)
    d.polygon([(390, 156), (398, 92), (336, 138)], fill=INNER_EAR)
    # 脸
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=FUR, outline=OUTLINE, width=6)
    # 胡须
    for dy in (-8, 8):
        d.line([cx - rx + 6, cy + 8 + dy, cx - rx - 40, cy + dy], fill=OUTLINE, width=4)
        d.line([cx + rx - 6, cy + 8 + dy, cx + rx + 40, cy + dy], fill=OUTLINE, width=4)
    ex = 58
    ly, ry_ = cy - 12, cy - 12
    lx, rxx = cx - ex, cx + ex

    if emo == "happy":
        d.arc([lx - 28, ly - 20, lx + 28, ly + 20], 200, 340, fill=DARK, width=9)
        d.arc([rxx - 28, ry_ - 20, rxx + 28, ry_ + 20], 200, 340, fill=DARK, width=9)
        d.ellipse([cx - 60, cy + 22, cx - 30, cy + 42], fill="#F6B9A0")
        d.ellipse([cx + 30, cy + 22, cx + 60, cy + 42], fill="#F6B9A0")
        d.arc([cx - 40, cy + 24, cx + 40, cy + 78], 0, 180, fill=DARK, width=8)
    elif emo == "sleep":
        d.arc([lx - 28, ly - 8, lx + 28, ly + 30], 20, 160, fill=DARK, width=8)
        d.arc([rxx - 28, ry_ - 8, rxx + 28, ry_ + 30], 20, 160, fill=DARK, width=8)
        d.arc([cx - 18, cy + 34, cx + 18, cy + 60], 0, 180, fill=DARK, width=6)
    elif emo == "angry":
        d.line([lx - 24, ly - 22, lx + 22, ly + 6], fill=DARK, width=8)
        d.line([rxx + 24, ry_ - 22, rxx - 22, ry_ + 6], fill=DARK, width=8)
        d.ellipse([lx - 13, ly, lx + 13, ly + 26], fill=DARK)
        d.ellipse([rxx - 13, ry_, rxx + 13, ry_ + 26], fill=DARK)
        d.arc([cx - 40, cy + 30, cx + 40, cy + 84], 180, 360, fill=DARK, width=8)
        d.line([cx - 26, cy + 40, cx - 8, cy + 58], fill="#FFFFFF", width=7)
        d.line([cx + 26, cy + 40, cx + 8, cy + 58], fill="#FFFFFF", width=7)
    elif emo == "confused":
        d.ellipse([lx - 22, ly - 26, lx + 22, ly + 18], fill=DARK)
        d.ellipse([rxx - 15, ry_ - 18, rxx + 15, ry_ + 12], fill=DARK)
        d.ellipse([lx - 16, ly - 20, lx - 4, ly - 8], fill="#FFFFFF")
        d.ellipse([rxx - 10, ry_ - 13, rxx - 2, ry_ - 5], fill="#FFFFFF")
        d.ellipse([cx - 12, cy + 44, cx + 12, cy + 66], fill=DARK)
    elif emo == "sad":
        d.arc([lx - 24, ly - 16, lx + 24, ly + 16], 20, 160, fill=DARK, width=8)
        d.arc([rxx - 24, ry_ - 16, rxx + 24, ry_ + 16], 20, 160, fill=DARK, width=8)
        d.line([lx - 22, ly - 34, lx + 18, ly - 26], fill=DARK, width=6)
        d.line([rxx + 22, ry_ - 34, rxx - 18, ry_ - 26], fill=DARK, width=6)
        d.ellipse([rxx + 8, ry_ + 16, rxx + 24, ry_ + 42], fill="#7FB6E8")
        d.arc([cx - 34, cy + 38, cx + 34, cy + 80], 180, 360, fill=DARK, width=7)
    elif emo == "proud":
        d.arc([lx - 26, ly - 26, lx + 26, ly + 14], 0, 180, fill=DARK, width=8)
        d.arc([rxx - 26, ry_ - 26, rxx + 26, ry_ + 14], 0, 180, fill=DARK, width=8)
        d.ellipse([lx - 12, ly - 8, lx + 12, ly + 16], fill=DARK)
        d.ellipse([rxx - 12, ry_ - 8, rxx + 12, ry_ + 16], fill=DARK)
        d.arc([cx - 34, cy + 30, cx + 34, cy + 70], 0, 180, fill=DARK, width=7)
    elif emo == "hungry":
        d.ellipse([lx - 22, ly - 24, lx + 22, ly + 22], fill=DARK)
        d.ellipse([rxx - 22, ry_ - 24, rxx + 22, ry_ + 22], fill=DARK)
        d.ellipse([lx - 15, ly - 17, lx + 1, ly - 1], fill="#FFFFFF")
        d.ellipse([rxx - 15, ry_ - 17, rxx + 1, ry_ - 1], fill="#FFFFFF")
        d.arc([cx - 30, cy + 34, cx + 30, cy + 72], 0, 180, fill=DARK, width=7)
        d.ellipse([cx + 26, cy + 46, cx + 40, cy + 74], fill="#9DD6F5")
    else:  # neutral
        d.ellipse([lx - 20, ly - 18, lx + 20, ly + 20], fill=DARK)
        d.ellipse([rxx - 20, ry_ - 18, rxx + 20, ry_ + 20], fill=DARK)
        d.ellipse([lx - 13, ly - 12, lx - 1, ly], fill="#FFFFFF")
        d.ellipse([rxx - 13, ry_ - 12, rxx - 1, ry_], fill="#FFFFFF")
        d.line([cx - 12, cy + 52, cx + 12, cy + 52], fill=DARK, width=6)

    # 鼻子
    d.polygon([(cx - 9, cy + 32), (cx + 9, cy + 32), (cx, cy + 44)], fill="#E08A5F")


def draw_accent(d, emo, idx):
    a = EMO[emo]["accent"]
    if not a:
        return
    col = ACCENT_COLORS.get(emo, OUTLINE)
    if emo == "sleep":
        for i, (x, y, s) in enumerate([(392, 96, 34), (420, 68, 44), (452, 40, 56)]):
            d.text((x, y), "z", font=load_font(s), fill=col)
    elif emo == "confused":
        d.text((384, 44), "?", font=F_BIG, fill=col)
    else:
        d.text((392, 46), a, font=F_MID, fill=col)


def draw_caption(d, emo, text, idx):
    col = ACCENT_COLORS.get(emo, "#B98A4E")
    d.rounded_rectangle([28, 406, 484, 492], radius=30, fill="#FFFFFF",
                        outline=col, width=4)
    bb = d.textbbox((0, 0), text, font=F_BIG)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((W - tw) / 2 - bb[0], (406 + 492) / 2 - th / 2 - bb[1]), text,
           font=F_BIG, fill=col)


def gen_one(emo, n, caption_idx, bg_idx):
    info = EMO[emo]
    img = Image.new("RGB", (W, H), info["bg"][bg_idx % len(info["bg"])])
    d = ImageDraw.Draw(img)
    # 背景点缀小圆
    d.ellipse([-60, -60, 120, 120], fill=info["bg"][(bg_idx + 1) % len(info["bg"])])
    d.ellipse([W - 90, H - 150, W + 60, H + 10], fill=info["bg"][(bg_idx + 2) % len(info["bg"])])
    draw_cat(d, emo, n)
    draw_accent(d, emo, n)
    draw_caption(d, emo, info["cap"][caption_idx % len(info["cap"])], n)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    made = []
    for emo in EMO:
        for j in range(3):                       # 每个情绪 3 张新图
            n = 2 + j
            cap = (j if emo != "happy" else j)
            img = gen_one(emo, n, cap, j)
            fn = "%s_%d.jpg" % (emo, n)
            fp = os.path.join(OUT, fn)
            img.save(fp, "JPEG", quality=90, optimize=True)
            made.append(fn)
    # 清单
    mani = os.path.join(OUT, "MANIFEST.txt")
    with open(mani, "w", encoding="utf-8", newline="") as f:
        f.write("小团子表情包扩充清单（v6）\n")
        f.write("总计新增 %d 张（512×512 JPG）\n" % len(made))
        f.write("emotion 值域：happy/hungry/sleep/angry/confused/sad/proud/neutral\n")
        f.write("-" * 40 + "\n")
        for fn in made:
            f.write("%s\t%dKB\n" % (fn, max(1, os.path.getsize(os.path.join(OUT, fn)) // 1024)))
    print("生成完成：%d 张 -> %s" % (len(made), OUT))
    for fn in made:
        print("  ", fn, os.path.getsize(os.path.join(OUT, fn)), "bytes")


if __name__ == "__main__":
    sys.exit(main())
