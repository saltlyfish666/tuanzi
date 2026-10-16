# -*- coding: utf-8 -*-
"""给 32 张表情写入触发条件备注（设备端沙箱内运行）"""
import json, os, time

META = "/root/tuanzi/memory/stickers.json"

NOTES = {
 "angry_1": "有点生气，瞪你一眼（被惹到时）",
 "angry_2": "不理你了！傲娇扭头生气",
 "angry_3": "气鼓鼓，腮帮子都鼓起来了",
 "angry_4": "气到炸毛，尾巴都竖起来了",
 "confused_1": "歪头疑惑：没听懂",
 "confused_2": "啊这…大脑宕机了",
 "confused_3": "啥啥啥？信息太多转不过弯",
 "confused_4": "没看懂，盯着发呆",
 "happy_1": "好耶！超级开心（被夸时）",
 "happy_2": "开心到飞起，蹦蹦跳跳",
 "happy_3": "嘿嘿嘿，偷偷乐",
 "happy_4": "开心转圈圈撒欢",
 "hungry_1": "嘴馋了，想吃东西",
 "hungry_2": "要吃饭饭，乖巧讨饭",
 "hungry_3": "小鱼干呢？点名要吃",
 "hungry_4": "肚肚咕咕叫，饿扁了",
 "neutral_1": "日常平静，安安静静",
 "neutral_2": "盯——专注看着你",
 "neutral_3": "本喵在呢，悠闲待命",
 "neutral_4": "安静蹲坐，陪着你",
 "proud_1": "傲娇得意，想被夸",
 "proud_2": "快夸夸我！求表扬",
 "proud_3": "哼哼~小得意",
 "proud_4": "小场面，故作淡定",
 "sad_1": "有点委屈，情绪低落",
 "sad_2": "呜…可怜巴巴",
 "sad_3": "委屈巴巴，需要哄",
 "sad_4": "耷耳朵，失落了",
 "sleep_1": "困了，打盹中",
 "sleep_2": "困死了喵，睁不开眼",
 "sleep_3": "呼呼…已经睡着了",
 "sleep_4": "要睡了，揉眼睛道晚安",
}

def main():
    meta = {}
    try:
        meta = json.load(open(META, encoding="utf-8"))
    except Exception:
        meta = {}
    files = meta.setdefault("files", {})
    n_new, n_upd = 0, 0
    for fn, note in NOTES.items():
        if not os.path.exists("/root/tuanzi/pics/" + fn + ".jpg"):
            continue
        ent = files.setdefault(fn + ".jpg", {})
        if not ent:
            ent["emotion"] = fn.split("_")[0]
            ent["added"] = int(time.time())
            ent["uses"] = 0
            n_new += 1
        if ent.get("note") != note:
            ent["note"] = note
            n_upd += 1
    json.dump(meta, open(META, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sum(1 for f in files if (files[f].get("note")))
    print("notes written: new=%d updated=%d total_with_note=%d files=%d" % (n_new, n_upd, total, len(files)))

if __name__ == "__main__":
    main()
