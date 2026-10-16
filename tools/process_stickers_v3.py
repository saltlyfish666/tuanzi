# -*- coding: utf-8 -*-
"""后处理：sticker_gen_v4/*.png → sticker_pack_v3/*.jpg（512x512, 质量90）"""
import os, glob
from PIL import Image

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sticker_gen_v4")
DST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sticker_pack_v3")

def main():
    os.makedirs(DST, exist_ok=True)
    n = 0
    for p in sorted(glob.glob(os.path.join(SRC, "*.png"))):
        name = os.path.basename(p).replace(".png", ".jpg")
        out = os.path.join(DST, name)
        im = Image.open(p).convert("RGB")
        im = im.resize((512, 512), Image.LANCZOS)
        im.save(out, "JPEG", quality=90)
        n += 1
        print("OK", name, os.path.getsize(out))
    print("TOTAL", n)

if __name__ == "__main__":
    main()
