# -*- coding: utf-8 -*-
"""批量生成 21 张表情包（本地 Z-Image Turbo，零积分）"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sticker_submit import submit, wait, fetch, OUT_DIR

BASE = ('Cute chibi kawaii cat sticker, hand-painted anime illustration style: '
        'a chubby fluffy grey striped tabby kitten, {scene}, '
        'bold Chinese text "{text}" at the top in white with thick black outline and slight tilt, '
        '{bg} background with {decor}, clean thick outlines, soft cel shading, '
        'high quality sticker art, square composition, character centered')

# (文件名, 场景, 文字, 背景, 装饰)
ITEMS = [
 ("happy_2.png", "jumping high with both paws raised in joy, huge happy grin, closed sparkling eyes, blush cheeks", "开心飞起", "warm cream", "pastel sparkles and tiny stars"),
 ("angry_2.png", "turning its head away with puffed angry cheeks and a frown, small red anger cloud beside head", "哼!", "soft pink", "small red anger puff clouds"),
 ("happy_4.png", "spinning around joyfully chasing its own tail, big smiling eyes, blush cheeks", "转圈圈", "soft cream", "pastel sparkles and tiny stars"),
 ("angry_3.png", "puffing up with puffed angry cheeks, frowning eyebrows, stomping front paws", "气鼓鼓", "soft pink", "small red anger puff clouds"),
 ("angry_4.png", "fur standing on end in a comical explosion of anger, wide angry eyes", "炸毛了喵", "light peach", "comic burst lines and small sparks"),
 ("confused_2.png", "tilting head sideways with big puzzled round eyes, one ear flopped", "嗯??", "soft blue", "floating question marks"),
 ("confused_3.png", "staring blankly with tiny bewildered eyes, mouth in a small o shape", "啥啥啥", "light lavender", "spinning swirl decorations"),
 ("confused_4.png", "looking down at a small open book with a puzzled expression", "没看懂喵", "soft mint", "question marks and tiny note doodles"),
 ("hungry_2.png", "pressing paws on its empty round belly with big watery pleading eyes", "饿饿", "warm beige", "tiny bread and fish doodles"),
 ("hungry_3.png", "holding up an empty bowl with sparkling hopeful eyes, drooling a little", "小鱼干呢", "soft yellow", "tiny fish icons"),
 ("hungry_4.png", "lying on its back holding its belly with a pitiful face, faint blush", "肚肚咕咕叫", "light cream", "small wobble lines and crumbs"),
 ("sad_2.png", "sitting with drooping ears and big teary eyes, one tear drop falling", "呜...", "soft grey blue", "tiny falling tear drops"),
 ("sad_3.png", "hugging its own tail with a wronged unhappy face, teary shiny eyes", "委屈巴巴", "light lavender", "small rain cloud doodles"),
 ("sad_4.png", "sitting quietly with ears flopped down sadly, looking at the ground", "耷耳朵", "soft mint", "tiny drooping flower"),
 ("sleep_2.png", "curled up in a tight ball with eyes closed tight, mid-yawn with tiny fangs", "困死了喵", "soft indigo", "zzz bubbles and small moon"),
 ("sleep_3.png", "sleeping peacefully curled up, gentle breathing with soft blush", "呼呼...", "light periwinkle", "zzz bubbles"),
 ("sleep_4.png", "rubbing its sleepy eyes with one paw, tiny nightcap on its head", "要睡了喵", "deep soft blue", "tiny stars and crescent moon"),
 ("proud_2.png", "standing tall with chest puffed out, smug confident smile, tail up high", "本喵厉害吧", "warm gold", "sparkle stars and a small crown"),
 ("proud_3.png", "smirking with half-closed smug eyes, arms crossed confidently", "哼哼~", "soft apricot", "tiny music notes"),
 ("proud_4.png", "sitting elegantly like a little boss with a relaxed proud expression", "小场面喵", "light champagne", "floating sparkles and a tiny medal"),
 ("neutral_2.png", "staring straight ahead with wide blank round eyes, totally still", "盯——", "soft grey", "tiny floating dots"),
 ("neutral_3.png", "sitting politely with a gentle neutral expression, tail wrapped around paws", "本喵在呢", "warm cream", "tiny paw prints"),
 ("neutral_4.png", "sitting quietly like a little statue with a calm face, blinking", "安静蹲坐", "soft sage", "tiny leaves and paw prints"),
]

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    total = len(ITEMS)
    for i, (fn, scene, text, bg, decor) in enumerate(ITEMS, 1):
        out = os.path.join(OUT_DIR, fn)
        if os.path.exists(out):
            print("[%d/%d] SKIP %s (exists)" % (i, total, fn), flush=True)
            continue
        prompt = BASE.format(scene=scene, text=text, bg=bg, decor=decor)
        try:
            t0 = time.time()
            pid = submit(prompt)
            entry = wait(pid)
            if not entry:
                print("[%d/%d] TIMEOUT %s" % (i, total, fn), flush=True)
                continue
            fetch(entry, out)
            print("[%d/%d] OK %s (%.0fs)" % (i, total, fn, time.time() - t0), flush=True)
        except Exception as e:
            print("[%d/%d] ERR %s: %s" % (i, total, fn, str(e)[:120]), flush=True)
            time.sleep(10)
    print("BATCH_DONE", flush=True)
