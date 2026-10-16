#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
panel_server.py — 小团子「前台可视化控制台」v3
================================================
平板浏览器打开 http://127.0.0.1:8898 ：
  - 连接拓扑图（引擎/NapCat/WS/MiMo/语音/天气/表情包/记忆/宿主管家）
  - 各组件实时状态 + 小白话解释（红/黄/灰分别什么意思、坏了按什么）
  - 运行统计 / 设置摘要 / 实时日志 / 登录二维码
  - 手动操作（均有二次确认 + 过程可视化 + 操作历史）：
      立即冒泡 / 重启引擎 / 重启NapCat / 测试MiMo / 暂停-恢复冒泡
  - /help 新手操作说明页（ help.html ）
动作通过 flag 文件交给宿主管家（tuanzi_host_agent.sh）执行；
面板每 5s 写 panel_heartbeat，宿主管家据此保活。
仅依赖标准库。
"""
import base64
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 沙盒默认 UTC 时区，强制北京时间（与引擎一致）
os.environ["TZ"] = "CST-8"
try:
    time.tzset()
except Exception:
    pass

BASE = "/root/tuanzi"
CFG_PATH = os.path.join(BASE, "config.json")
STATUS_PATH = os.path.join(BASE, "status.json")
PICS_DIR = os.path.join(BASE, "pics")
STK_META = os.path.join(BASE, "memory", "stickers.json")
STICKER_DAY = os.path.join(BASE, "stickers_day.json")
STK_WLOCK = threading.Lock()   # 面板写元数据的进程内锁（ThreadingHTTPServer 并发请求防互相覆盖）
CFG_WLOCK = threading.Lock()   # 面板写 config.json 的进程内锁（并发保存设置防互相覆盖）
IMG_EXTS = (".jpg", ".jpeg", ".png", ".gif")
EMOTIONS = ("happy", "hungry", "sleep", "angry", "confused", "sad", "proud", "neutral")
MEM_DIR = os.path.join(BASE, "memory")
DIARY_PATH = os.path.join(BASE, "diary.json")
SERVICE_LOG = os.path.join(BASE, "service.log")
QR_DIR = os.path.join(BASE, "qr")
HELP_PATH = os.path.join(BASE, "help.html")
DASH_PATH = os.path.join(BASE, "dashboard.html")


def load_dashboard():
    """v4 新界面：从 dashboard.html 实时读取（可热更新，不用重启面板）；
    文件缺失时回退内嵌的 v3 版本。"""
    try:
        with open(DASH_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return DASHBOARD_HTML
NAPCAT_LOG = "/data/local/tmp/napcat_dbg.log"   # /data/local/tmp 已 bind 进沙盒，可直接读
FLAG_DIR = "/data/local/tmp/tuanzi_flags"
HEARTBEAT = os.path.join(FLAG_DIR, "panel_heartbeat")
NAPCAT_HTTP = "http://127.0.0.1:3000"
PORT = 8898

# 每个配件的「小白话」解释：(它是干嘛的, 出问题了怎么办)
COMP_HINT = {
    "engine": ("小团子的大脑和嘴巴：想话题、回消息、主动找你聊天。",
               "红色=它睡着了：点下面的「重启小团子引擎」叫醒它，10 秒就好，聊天记录不丢。"),
    "napcat": ("小团子登 QQ 的通道，消息全靠它送出去。",
               "红色=掉线了：点「重启 NapCat」。如果还要登录，页面底部会出现二维码，用手机 QQ 扫一下。"),
    "ws": ("引擎和 QQ 通道之间的电话线。",
          "红色=电话断了：引擎会自动重拨，不用管；一直红就重启引擎。"),
    "mimo": ("小团子的思考能力（AI 大脑），聊天内容都是它现想的。",
             "黄色=想太多被限流了，歇 60 秒自己好；红色=Key 可能欠费，找管理员。"),
    "tts": ("把文字变成小团子猫叫声的工具。",
            "灰色=待命还没发声，正常的；一直不动可以重启引擎。"),
    "weather": ("小团子看天气说话用（下雨会提醒你带伞）。",
                "灰色=待命；一直不动可以重启引擎。"),
    "sticker": ("小团子的表情包库存。",
                "红色=库存空了，找管理员补图。"),
    "memory": ("小团子记住每个人聊过什么，数字越大记越多。",
               "灰色=还没聊过天，正常的。"),
    "agent": ("系统里的小保姆：引擎/面板出事它自动拉起来，你点的按钮也靠它执行。",
              "红色=小保姆跑了：找管理员在设备上重新启动「宿主管家」。"),
}


# 每个部件出问题时的「平板本机可做」修复步骤（不用碰电脑）
COMP_STEPS = {
    "engine": [
        "在这页点「摇醒它」——最常用，约 10 秒见效",
        "点了没反应？等 30 秒：小保姆每 3 秒巡一次，会自己把它拉起来",
        "还不行就看「宿主管家」那张卡；它也红了才需要工程师出手",
        "放心：聊天记忆存在平板本地文件里，重启不会丢",
    ],
    "napcat": [
        "在这页点「帮它重连 QQ」，约 30 秒",
        "页面底部会出现二维码 → 用手机 QQ 扫它（这一步只能你本人做）",
        "反复掉线 → 把平板 WiFi 开关一次，再点一次重连",
        "二维码扫不上 → 刷新本页拿新码，旧码几分钟就过期",
    ],
    "ws": [
        "先不用管：引擎每 5 秒自己重拨一次",
        "一直红、QQ 里也没消息 → 点「摇醒它」",
        "摇完还红 → 看「NapCat·QQ」卡是否也红，是就按那张卡的步骤走",
    ],
    "mimo": [
        "黄色=想太多被限流：等 60 秒自己恢复，别重复点",
        "红色超过 10 分钟 → 点「考考它的大脑」验证一下",
        "还是不行 → 检查平板能不能上网（浏览器随便开个网页）",
        "网络正常仍红 → 在这页「它的大脑」里换 Key 或换模型（有 DeepSeek 一键模版）",
    ],
    "tts": [
        "灰色=待命，本来就是正常的，不用修",
        "想验证：在 QQ 里跟它说句话，看它发不发语音条",
        "连着几条都不出声 → 点「摇醒它」；文字聊天不受影响",
    ],
    "weather": [
        "灰色/黄色都不影响聊天，它每 20 分钟自己去续一次",
        "一直不更新 → 先看平板网络，再点「摇醒它」",
        "城市报错了 → 在这页「🌤 天气城市」改好保存",
    ],
    "sticker": [
        "点开这张卡 → 里面就是表情包仓库：可以自己传图、给每张写设定、删掉不喜欢的",
        "仓库空了也不影响文字聊天，随便传一张就恢复",
        "「备注」里写触发词（用 | 分开，比如：晚安|睡觉|困），聊天里说到就会优先用这张",
        "传太多乱的？点开卡里每张的「删」，会进回收夹，不真删",
    ],
    "memory": [
        "灰色=还没聊过天，正常",
        "数字应该只增不减；看着变小多半是统计口径重启，不代表失忆",
        "记忆文件就在平板本地，重启引擎不会清空",
    ],
    "agent": [
        "这是最要紧的一张卡：它红了，面板上的按钮会全部失效",
        "先随便点个按钮试试：有反应=只是心跳延迟，等 1 分钟刷新",
        "完全没反应 → 需要工程师在平板上重启「宿主管家」",
        "在那之前，聊天和记忆这类核心功能其实还在工作",
    ],
}


def log(msg):
    try:
        with open(os.path.join(BASE, "panel.log"), "a") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    """原子写（与引擎同款：tmp + os.replace，防写一半断电损坏）"""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def http_get(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"__err__": str(e)[:80]}


def tail(path, n):
    """读日志尾部；每行硬截断到 150 字符（NapCat 调试日志会吐出几十万字符的
    hex/blob 单行，不截断会把平板浏览器直接撑爆）。
    v4: seek 只读文件末尾 64KB——原来 f.read() 全文件读取，3 秒刷新一次时
    大日志会持续造成内存 churn 和 CPU 浪费。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            data = f.read()
        lines = data.decode("utf-8", errors="replace").splitlines()
        if size > 65536 and lines:
            lines = lines[1:]  # 首行可能被截断，丢弃
        out = []
        for l in lines[-n:]:
            out.append(l[:150] + ("…" if len(l) > 150 else ""))
        return out
    except Exception:
        return []


def mtime_age(path):
    try:
        return max(0, int(time.time() - os.path.getmtime(path)))
    except Exception:
        return 999999


def fmt_age(sec):
    sec = int(sec)
    if sec < 0:
        return "从未"
    if sec < 60:
        return "%d 秒" % sec
    if sec < 3600:
        return "%d 分钟" % (sec // 60)
    if sec < 86400:
        return "%d 小时" % (sec // 3600)
    return "%d 天" % (sec // 86400)


def fmt_dur(sec):
    sec = int(sec)
    return "%d 小时 %02d 分" % (sec // 3600, (sec % 3600) // 60)


# ---------------- 状态采集 ----------------
def probe_napcat():
    out = {"online": False, "nickname": "", "user_id": "", "err": ""}
    d = http_get(NAPCAT_HTTP + "/get_status", timeout=4)
    if "__err__" in d:
        out["err"] = d["__err__"]
    else:
        data = d.get("data") or {}
        out["online"] = bool(d.get("status") == "ok" or data.get("online"))
    d2 = http_get(NAPCAT_HTTP + "/get_login_info", timeout=4)
    data2 = d2.get("data") or {}
    if data2.get("user_id"):
        out["user_id"] = str(data2.get("user_id"))
        out["nickname"] = data2.get("nickname", "")
    return out


def scan_memory(limit=8):
    """一次遍历 memory 目录：返回 (files, rounds, recent)。
    recent = 跨会话最新 limit 条对话（时间升序），供面板「最近的对话」"""
    files = rounds = 0
    recent = []
    names = {}
    try:
        c = load_json(os.path.join(BASE, "contacts.json"), {})
        for k, v in (c.get("users") or {}).items():
            names[str(k)] = (v or {}).get("name") or ""
        for k, v in (c.get("groups") or {}).items():
            names[str(k)] = (v or {}).get("name") or ("群" + str(k))
    except Exception:
        pass
    try:
        for fn in os.listdir(MEM_DIR):
            if not fn.endswith(".json"):
                continue
            files += 1
            try:
                with open(os.path.join(MEM_DIR, fn), "r", encoding="utf-8") as f:
                    arr = json.load(f)
            except Exception:
                continue
            if not isinstance(arr, list):
                continue
            rounds += len(arr)
            kind, _, tid = fn[:-5].partition("_")
            who = names.get(tid) or ("群" + tid if kind == "groups" else ("好友" + tid))
            for m in arr[-12:]:
                ts = float(m.get("ts", 0) or 0)
                recent.append({"who": who, "role": m.get("role", ""),
                               "text": (m.get("content") or "")[:60],
                               "age_sec": (max(0, int(time.time() - ts)) if ts else 999999)})
    except Exception:
        pass
    recent.sort(key=lambda x: x["age_sec"])
    return files, rounds, list(reversed(recent[:limit]))


def scan_persons():
    """分人记忆档案（memory/persons/）：每个人一份跨场合时间线（W4）"""
    out = []
    try:
        c = load_json(os.path.join(BASE, "contacts.json"), {})
        names = {}
        for k, v in (c.get("users") or {}).items():
            names[str(k)] = ((v or {}).get("name") or "").strip()
        owner = str((load_json(CFG_PATH, {}) or {}).get("owner_qq") or "")
        d = os.path.join(MEM_DIR, "persons")
        for fn in os.listdir(d):
            if not (fn.startswith("person_") and fn.endswith(".json")):
                continue
            uid = fn[len("person_"):-len(".json")]
            arr = load_json(os.path.join(d, fn), [])
            if not isinstance(arr, list) or not arr:
                continue
            last = arr[-1] if isinstance(arr[-1], dict) else {}
            ts = float(last.get("ts", 0) or 0)
            nm = (names.get(uid) or "").strip("\u3164\u2800\u200b\u200c\u200d\ufeff").strip()
            if not nm:
                nm = "主人" if uid == owner else ("好友" + uid)
            out.append({"uid": uid, "name": nm[:16], "n": len(arr),
                        "age_sec": (max(0, int(time.time() - ts)) if ts else 999999),
                        "preview": [(m.get("content") or "")[:56] for m in arr[-3:]
                                    if isinstance(m, dict)]})
    except Exception:
        pass
    out.sort(key=lambda x: x["age_sec"])
    return out


def count_stickers():
    try:
        return len([f for f in os.listdir(PICS_DIR)
                    if f.lower().endswith((".jpg", ".png", ".jpeg", ".gif"))])
    except Exception:
        return 0


def sticker_list():
    """表情包仓库列表（面板管理弹层用）：文件 + 情绪/备注/用量（元数据优先，文件名兜底）"""
    try:
        meta = load_json(STK_META, {}) or {}
        files_meta = meta.get("files") or {}
        out = []
        for f in sorted(os.listdir(PICS_DIR)):
            if not f.lower().endswith((".jpg", ".png", ".jpeg", ".gif")):
                continue
            fp = os.path.join(PICS_DIR, f)
            if not os.path.isfile(fp):
                continue
            ent = files_meta.get(f) or {}
            emo = str(ent.get("emotion") or "").strip().lower()
            if emo not in EMOTIONS:
                b = f.split("_", 1)[0].lower()
                emo = b if b in EMOTIONS else ""
            try:
                size_kb = max(1, os.path.getsize(fp) // 1024)
            except Exception:
                size_kb = 0
            out.append({
                "file": f,
                "emotion": emo,
                "note": str(ent.get("note") or "")[:60],
                "uses": int(ent.get("uses", 0) or 0),
                "last_used": str(ent.get("last_used") or ""),
                "size_kb": size_kb,
            })
        out.sort(key=lambda x: (-x["uses"], x["file"]))
        return out
    except Exception:
        return []


def sticker_today_count():
    d = load_json(STICKER_DAY, {})
    if d.get("date") != time.strftime("%Y-%m-%d"):
        return 0
    return int(d.get("n", 0) or 0)


def sticker_safe_name(name):
    """文件名安全化：去路径、去危险字符、限长（防 ../ 与奇怪字符）"""
    name = os.path.basename(str(name or "")).strip()
    name = name.replace("\\", "_").replace("/", "_")
    if not name or name.startswith("."):
        return ""
    if len(name) > 80:
        root, ext = os.path.splitext(name)
        name = root[:80 - len(ext)] + ext
    return name


def sticker_save(fname, emotion, note):
    """保存一张表情包的设定（情绪 + 备注触发词）"""
    fname = os.path.basename(str(fname or ""))
    if not fname or not os.path.isfile(os.path.join(PICS_DIR, fname)):
        return {"ok": False, "msg": "这张表情包找不到"}
    emo = str(emotion or "").strip().lower()
    if emo not in EMOTIONS:
        emo = ""
    note = re.sub(r"\s+", " ", str(note or "")).strip()[:60]
    with STK_WLOCK:
        meta = load_json(STK_META, {}) or {}
        files_meta = meta.setdefault("files", {})
        ent = files_meta.setdefault(fname, {})
        ent["emotion"] = emo
        ent["note"] = note
        ent.setdefault("added", int(time.time()))
        save_json(STK_META, meta)
    return {"ok": True, "msg": "「%s」的设定已保存" % fname[:20]}


def sticker_del(fname):
    """把表情包移进 pics/_trash 回收夹（不真删，可找回）"""
    fname = os.path.basename(str(fname or ""))
    fp = os.path.join(PICS_DIR, fname)
    if not fname or not os.path.isfile(fp):
        return {"ok": False, "msg": "这张表情包找不到"}
    try:
        trash = os.path.join(PICS_DIR, "_trash")
        os.makedirs(trash, exist_ok=True)
        dst = os.path.join(trash, fname)
        if os.path.exists(dst):
            dst = os.path.join(trash, time.strftime("%H%M%S_") + fname)
        os.replace(fp, dst)
    except Exception as e:
        return {"ok": False, "msg": "移不动：%s" % e}
    with STK_WLOCK:
        meta = load_json(STK_META, {}) or {}
        (meta.get("files") or {}).pop(fname, None)
        save_json(STK_META, meta)
    return {"ok": True, "msg": "「%s」已移进回收夹（可找回）" % fname[:20]}


def sticker_upload(name, b64):
    """上传一张表情包（base64）：扩展名白名单 + 魔数校验 + 5MB 上限 + 重名自动加序号"""
    raw = sticker_safe_name(name)
    if not raw:
        return {"ok": False, "msg": "文件名不对"}
    ext = os.path.splitext(raw)[1].lower()
    if ext not in IMG_EXTS:
        return {"ok": False, "msg": "只收 jpg / png / gif 图"}
    b64 = str(b64 or "").strip()
    if b64.startswith("data:") and "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        data = base64.b64decode(b64)
    except Exception:
        return {"ok": False, "msg": "图片数据不完整（没传全）"}
    if not data:
        return {"ok": False, "msg": "图片是空的"}
    if len(data) > 5 * 1024 * 1024:
        return {"ok": False, "msg": "图太大啦（5MB 以内）"}
    ok_magic = ((ext in (".jpg", ".jpeg") and data[:3] == b"\xff\xd8\xff") or
                (ext == ".png" and data[:8] == b"\x89PNG\r\n\x1a\n") or
                (ext == ".gif" and data[:4] == b"GIF8"))
    if not ok_magic:
        return {"ok": False, "msg": "图片内容和后缀对不上，换一张试试"}
    os.makedirs(PICS_DIR, exist_ok=True)
    with STK_WLOCK:                  # 去重命名 + 落盘 + 元数据一体（防并发上传撞名/覆盖）
        root, i = os.path.splitext(raw)[0], 0
        fname = raw
        while os.path.exists(os.path.join(PICS_DIR, fname)):
            i += 1
            fname = "%s_%d%s" % (root[:60], i, ext)
        try:
            with open(os.path.join(PICS_DIR, fname), "wb") as f:
                f.write(data)
        except Exception as e:
            return {"ok": False, "msg": "存不下：%s" % e}
        meta = load_json(STK_META, {}) or {}
        files_meta = meta.setdefault("files", {})
        files_meta[fname] = {"emotion": "", "note": "", "uses": 0, "added": int(time.time())}
        save_json(STK_META, meta)
    return {"ok": True, "file": fname,
            "msg": "已收进仓库：%s（%dKB），点「编辑」给它写设定" % (fname[:20], max(1, len(data) // 1024))}


def said_list(limit=60):
    """今天的语录（面板「今天说话」点开看的逐句列表），倒序=最近的在前"""
    try:
        d = load_json(os.path.join(BASE, "daily_said.json"), {})
        if d.get("date") != time.strftime("%Y-%m-%d"):
            return []
        out = []
        for it in (d.get("items") or [])[-limit:]:
            ts = float(it.get("ts", 0) or 0)
            out.append({"who": it.get("who") or "",
                        "grp": bool(it.get("grp")),
                        "text": (it.get("text") or "")[:120],
                        "age_sec": (max(0, int(time.time() - ts)) if ts else 0)})
        out.reverse()
        return out
    except Exception:
        return []


def tokens_detail():
    """今天的小鱼干明细（喂进去 vs 吐出来）"""
    try:
        d = load_json(os.path.join(BASE, "tokens.json"), {})
        r = d.get(time.strftime("%Y-%m-%d")) or {}
        return {"prompt": int(r.get("prompt", 0) or 0),
                "completion": int(r.get("completion", 0) or 0),
                "calls": int(r.get("calls", 0) or 0)}
    except Exception:
        return {"prompt": 0, "completion": 0, "calls": 0}


# ==================== 功能项详情（v6：点开「全部功能一览」里的每一项） ====================
_INVISIBLE = "\u3164\u2800\u200b\u200c\u200d\ufeff"
# 由「互动轮数」估算熟悉度等级（引擎没有每人独立亲密度，这里只做展示用估算）
_MEM_LEVELS = ((86, "一家人"), (61, "超级要好"), (36, "好朋友"), (16, "熟人了"), (0, "刚认识"))


def _mem_level(rounds):
    try:
        for th, name in _MEM_LEVELS:
            if int(rounds) >= th:
                return name
    except Exception:
        pass
    return "刚认识"


def detail_memory_persons():
    """分人记忆详情：扫 memory/persons/*.json → 每人档案摘要（含长期记忆 facts 前 5 条）"""
    out = []
    try:
        c = load_json(os.path.join(BASE, "contacts.json"), {})
        names = {}
        for kk, vv in (c.get("users") or {}).items():
            names[str(kk)] = ((vv or {}).get("name") or "").strip()
        owner = str((load_json(CFG_PATH, {}) or {}).get("owner_qq") or "")
        facts_all = (load_json(os.path.join(BASE, "facts.json"), {}) or {}).get("users") or {}
        d = os.path.join(MEM_DIR, "persons")
        for fn in sorted(os.listdir(d)):
            if not (fn.startswith("person_") and fn.endswith(".json")):
                continue
            uid = fn[len("person_"):-len(".json")]
            arr = load_json(os.path.join(d, fn), [])
            if not isinstance(arr, list) or not arr:
                continue
            last = arr[-1] if isinstance(arr[-1], dict) else {}
            ts = float(last.get("ts", 0) or 0)
            nm = (names.get(uid) or "").strip(_INVISIBLE).strip()
            if not nm:
                nm = "主人" if uid == owner else ("好友" + uid)
            fs = facts_all.get(uid) or {}
            facts = [str(f.get("text") or "") for f in (fs.get("facts") or [])
                     if isinstance(f, dict)][:5]
            out.append({"uid": uid, "name": nm[:16], "rounds": len(arr),
                        "intimacy": _mem_level(len(arr)),
                        "last": (time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else ""),
                        "last_age": (max(0, int(time.time() - ts)) if ts else 999999),
                        "facts": facts})
    except Exception:
        pass
    out.sort(key=lambda x: x["last_age"])
    return out


def detail_relay_tasks():
    """定时转达任务清单（relay_schedule.json 里只存未完成的，所以 done 恒为 False）"""
    out = []
    try:
        d = load_json(os.path.join(BASE, "relay_schedule.json"), {"tasks": []})
        for t in (d.get("tasks") or []):
            if not isinstance(t, dict):
                continue
            fts = float(t.get("fire_ts", 0) or 0)
            when = time.strftime("%m-%d %H:%M", time.localtime(fts)) if fts else ""
            out.append({"when": when, "to": str(t.get("who") or ""),
                        "text": str(t.get("what") or ""), "done": False})
    except Exception:
        pass
    out.sort(key=lambda x: x.get("when", ""))
    return out


def detail_api(k):
    """GET /api/detail?k=xxx → 功能项详情弹层数据。任何一项读不到都返回空壳，绝不抛异常。"""
    k = str(k or "").strip().lower()
    cfg = load_json(CFG_PATH, {}) or {}
    st = load_json(STATUS_PATH, {}) or {}
    if k == "vision":
        model = ""
        try:
            mk = open("/data/local/tmp/llama_model.txt", "r", errors="ignore").read().strip()
            if mk:
                model = mk.replace("models/", "").replace("-Q4_K_M", "").replace(".gguf", "")
        except Exception:
            pass
        vis = os.path.exists("/root/models/mmproj-Qwen3-VL-4B-F16.gguf")
        if not model:
            model = "Qwen2.5-VL-3B"
        return {"ok": True, "k": k, "model": model, "vision": bool(vis),
                "note": "约40秒看一张图" if vis else "装上视觉模型后就能看图了"}
    if k == "chat":
        persons = scan_persons()
        return {"ok": True, "k": k,
                "c2c_enabled": bool(cfg.get("c2c_enabled", True)),
                "group_enabled": bool(cfg.get("group_enabled", True)),
                "replies_today": int(st.get("said_today", 0) or 0),
                "persons": len(persons)}
    if k == "memory":
        return {"ok": True, "k": k, "persons": detail_memory_persons()}
    if k == "relay":
        return {"ok": True, "k": k, "tasks": detail_relay_tasks()}
    if k == "qzone":
        en = bool(cfg.get("qzone_watch", True))
        qs = load_json(os.path.join(BASE, "qzone_watch.json"), {}) or {}
        ck = float(qs.get("checked_ts", 0) or 0)
        owner = str(cfg.get("owner_qq") or "")
        watched = (["主人 " + owner] if owner else [])
        return {"ok": True, "k": k, "enabled": en,
                "last_check": (time.strftime("%m-%d %H:%M", time.localtime(ck)) if ck else ""),
                "last_tid": str(qs.get("last_tid") or ""), "watched": watched}
    if k == "sticker":
        imgs = []
        notes = {}
        mfiles = {}
        try:
            _meta = load_json(os.path.join(BASE, "memory", "stickers.json"), {}) or {}
            mfiles = _meta.get("files") or {}
        except Exception:
            mfiles = {}
        try:
            for f in sorted(os.listdir(PICS_DIR)):
                if f.lower().endswith(IMG_EXTS):
                    imgs.append("/stk/" + f)
                    _nt = (mfiles.get(f) or {}).get("note") or ""
                    if _nt:
                        notes[f] = _nt
        except Exception:
            pass
        return {"ok": True, "k": k, "count": len(imgs), "images": imgs[:36], "notes": notes}
    if k == "greet":
        return {"ok": True, "k": k,
                "enabled": bool(cfg.get("greet_enabled", True)),
                "morning_hour": int(float(cfg.get("greet_morning_hour", 8))),
                "evening_hour": int(float(cfg.get("greet_evening_hour", 22)))}
    if k == "night":
        return {"ok": True, "k": k,
                "enabled": bool(cfg.get("night_enabled", True)),
                "start": int(float(cfg.get("night_start", 0))),
                "end": int(float(cfg.get("night_end", 8)))}
    if k == "poke":
        return {"ok": True, "k": k, "enabled": bool(cfg.get("poke_enabled", True))}
    if k == "weather":
        age = int(st.get("weather_age", -1))
        return {"ok": True, "k": k, "enabled": bool(cfg.get("weather_enabled", True)),
                "city": str(cfg.get("city", "北京")), "text": str(st.get("weather", "")),
                "age": age,
                "note": ("变天（下雨/降温）时它会主动提醒你带伞、加衣服。"
                         "关掉后它就不再关心天气，也不会提醒了。")}
    return {"ok": False, "k": k, "msg": "这个详情还没有"}


def collect():
    now = time.time()
    st = load_json(STATUS_PATH, {})
    engine_age = mtime_age(STATUS_PATH) if os.path.exists(STATUS_PATH) else 999999
    engine_online = engine_age < 30
    napcat = probe_napcat()
    agent_age = mtime_age(os.path.join(FLAG_DIR, "agent_heartbeat"))
    mem_files, mem_rounds, recent = scan_memory()
    persons = scan_persons()
    sticker_n = count_stickers()

    llm_ok = int(st.get("llm_ok", 0))
    llm_fail = int(st.get("llm_fail", 0))
    llm_last_ok = float(st.get("llm_last_ok_ts", 0) or 0)
    llm_last_fail = float(st.get("llm_last_fail_ts", 0) or 0)
    llm_cool = int(st.get("llm_cool", 0))
    voice_last = float(st.get("voice_last_ts", 0) or 0)
    voice_fail = float(st.get("voice_last_fail_ts", 0) or 0)
    weather_age = int(st.get("weather_age", -1))
    ws_state = st.get("ws_state", "closed")
    last_event_age = int(st.get("last_event_age", 999999))

    # ---- 本地大脑检测（自适应状态卡）----
    _cfg0 = load_json(CFG_PATH, {})
    _is_local = str(_cfg0.get("llm_backend", "mimo")) == "local"
    _llama_alive = False
    _llama_model = ""
    try:
        import socket as _sk0
        _s0 = _sk0.create_connection(("127.0.0.1", 8080), timeout=2)
        _s0.close()
        _llama_alive = True
    except Exception:
        pass
    if _llama_alive:
        try:
            _mk = open("/data/local/tmp/llama_model.txt", "r", errors="ignore").read().strip()
            if _mk:
                _llama_model = _mk.replace("models/", "").replace("-Q4_K_M", "").replace(".gguf", "")
        except Exception:
            pass
    if _llama_alive and not _llama_model:
        try:
            import re as _re0
            _l0 = open("/data/local/tmp/llama_server.log", "r", errors="ignore").read()[-6000:]
            _m0 = _re0.findall(r"models/([A-Za-z0-9._\-]+)\.gguf", _l0)
            if _m0:
                _llama_model = _m0[-1].replace("-Q4_K_M", "").replace(".gguf", "")
        except Exception:
            pass

    # ---- 大脑（MiMo/本地 自适应）----
    if _is_local:
        mimo_name = "本地大脑"
        if _llama_alive:
            mimo = ("online", "本地 %s · 现场计算 · 免费" % (_llama_model or "小模型"))
        else:
            mimo = ("warn", "本地推理服务未运行（管家会自动拉起）")
    else:
        mimo_name = "MiMo 大脑"
        if False:
            pass
        elif llm_cool > 0:
            mimo = ("warn", "限流冷却中，%d 秒后恢复" % llm_cool)
        elif llm_last_ok and now - llm_last_ok < 1800:
            mimo = ("online", "正常 · 最近调用 %s 前" % fmt_age(now - llm_last_ok))
        elif llm_last_fail and llm_last_fail > llm_last_ok:
            mimo = ("offline", "连续失败 · 最后错误 %s 前" % fmt_age(now - llm_last_fail))
        elif llm_ok > 0:
            mimo = ("idle", "待命 · 累计成功 %d 次" % llm_ok)
        else:
            mimo = ("idle", "待命 · 本次启动还未调用")

    # ---- Edge-TTS 语音 ----
    if voice_last and now - voice_last < 7200:
        tts = ("online", "正常 · 最近发声 %s 前" % fmt_age(now - voice_last))
    elif voice_fail and voice_fail > voice_last:
        tts = ("warn", "上次合成失败 %s 前" % fmt_age(now - voice_fail))
    elif voice_last:
        tts = ("idle", "待命 · 累计 %d 条" % int(st.get("voices", 0)))
    else:
        tts = ("idle", "待命 · 本次启动还未发声")

    # ---- 天气 ----
    if weather_age >= 0 and weather_age < 1800:
        weather = ("online", "%s · %s 前更新" % (st.get("weather", ""), fmt_age(weather_age)))
    elif weather_age >= 0:
        weather = ("warn", "缓存过期（%s 前）" % fmt_age(weather_age))
    else:
        weather = ("idle", "本次启动还未获取")

    # ---- WS 长连接 ----
    if ws_state == "open" and last_event_age < 400:
        ws = ("online", "已连接 · 最后事件 %s 前" % fmt_age(last_event_age))
    elif ws_state == "open":
        ws = ("warn", "已连接但 %s 无事件（假活？）" % fmt_age(last_event_age))
    else:
        ws = ("offline", "未连接（引擎会自动重连）")

    # ---- 宿主管家 ----
    if agent_age < 20:
        agent = ("online", "在线 · 心跳 %d 秒前" % agent_age)
    elif agent_age < 60:
        agent = ("warn", "心跳超龄 %d 秒" % agent_age)
    else:
        agent = ("offline", "无心跳 %s（自愈将失效！）" % fmt_age(agent_age))

    comps = [
        {"id": "engine", "name": "小团子引擎", "icon": "🐱",
         "status": "online" if engine_online else "offline",
         "detail": ("运行 %s · 事件 %s · 回复 %s · 冒泡 %s"
                    % (fmt_dur(int(st.get("uptime", 0))), st.get("events", 0),
                       st.get("replies", 0), st.get("bubbles", 0))) if engine_online
                   else "状态文件 %s 未更新（引擎可能已退出）" % fmt_age(engine_age)},
        {"id": "napcat", "name": "NapCat·QQ", "icon": "🤖",
         "status": "online" if napcat["online"] else "offline",
         "detail": ("%s(%s)" % (napcat["nickname"], napcat["user_id"])) if napcat["online"]
                   else ("HTTP 3000 不通：%s" % (napcat.get("err") or "未响应"))},
        {"id": "ws", "name": "WS 长连接", "icon": "🔌", "status": ws[0], "detail": ws[1]},
        {"id": "mimo", "name": mimo_name, "icon": "🧠", "status": mimo[0], "detail": mimo[1]},
        {"id": "tts", "name": "Edge-TTS 语音", "icon": "🔊", "status": tts[0], "detail": tts[1]},
        {"id": "weather", "name": "天气服务", "icon": "🌤️", "status": weather[0], "detail": weather[1]},
        {"id": "sticker", "name": "表情包", "icon": "😸",
         "status": "online" if sticker_n else "offline",
         "detail": ("%d 张待命 · 今天甩了 %d 张" % (sticker_n, sticker_today_count()))
                   if sticker_n else "仓库空了（点开这张卡可以自己传图）"},
        {"id": "memory", "name": "对话记忆", "icon": "💾",
         "status": "online" if mem_files else "idle",
         "detail": "%d 个会话 · %d 条记忆 · %d 人档案" % (mem_files, mem_rounds, len(persons))},
        {"id": "agent", "name": "宿主管家", "icon": "🛡️", "status": agent[0], "detail": agent[1]},
    ]
    for c in comps:
        h = COMP_HINT.get(c["id"], ("", ""))
        c["hint_plain"] = h[0]
        c["hint_fix"] = h[1]
        c["fix_steps"] = COMP_STEPS.get(c["id"], [])

    # 总体状态
    bad = [c for c in comps if c["status"] == "offline" and c["id"] in ("engine", "napcat", "ws")]
    warn = [c for c in comps if c["status"] == "warn" or
            (c["status"] == "offline" and c["id"] not in ("engine", "napcat", "ws"))]
    if bad:
        overall = "bad"
    elif warn:
        overall = "warn"
    else:
        overall = "ok"

    # 二维码（NapCat 掉线登录用）：15 分钟内视为有效
    qr_path = os.path.join(QR_DIR, "napcat_qr.png")
    qr_age = mtime_age(qr_path) if os.path.exists(qr_path) else 999999
    qr_fresh = qr_age < 900 and not napcat["online"]

    cfg = load_json(CFG_PATH, {})
    key = (cfg.get("mimo_key") or "").strip()
    cfg_view = {
        "模型": cfg.get("mimo_model", ""),
        "MiMo Key": ("%s…%s" % (key[:6], key[-4:])) if len(key) > 12 else ("已配置" if key else "未配置"),
        "语音音色": cfg.get("voice_name", ""),
        "语音音调": "%s / 语速 %s" % (cfg.get("voice_pitch", ""), cfg.get("voice_rate", "")),
        "语音概率": "%d%%（语音/文字对半）" % int(float(cfg.get("voice_prob", 0.5)) * 100),
        "表情包概率": "%d%%" % int(float(cfg.get("sticker_prob", 0.22)) * 100),
        "表情包配文": "%d%%（它单独甩图时，随口配句话的概率）" % int(float(cfg.get("sticker_caption_prob", 0.35)) * 100),
        "插嘴概率": "%d%%" % int(float(cfg.get("interject_prob", 0.22)) * 100),
        "热聊窗口": "%s 秒" % cfg.get("hot_window", 180),
        "冒泡间隔": "%s~%s 分钟" % (cfg.get("bubble_min_min", 18), cfg.get("bubble_max_min", 55)),
        "每日冒泡上限": "%s 条（今日 %s）" % (cfg.get("max_bubbles_per_day", 20), st.get("bubble_today", 0)),
        "深夜免打扰": "%s:00~%s:00" % (cfg.get("night_start", 0), cfg.get("night_end", 8)),
        "城市": "%s（%s,%s）" % (cfg.get("city", "北京"), cfg.get("lat", ""), cfg.get("lon", "")),
        "记忆轮数": "%s 轮/人" % cfg.get("hist_max", 24),
    }

    # ---- 大脑形态（本地大模型卡片数据）----
    llama_alive = False
    llama_model = ""
    llama_vision = os.path.exists("/root/models/mmproj-Qwen3-VL-4B-F16.gguf")
    try:
        import socket as _sk
        _sock = _sk.create_connection(("127.0.0.1", 8080), timeout=2)
        _sock.close()
        llama_alive = True
    except Exception:
        llama_alive = False
    if llama_alive:
        try:
            _mk2 = open("/data/local/tmp/llama_model.txt", "r", errors="ignore").read().strip()
            if _mk2:
                llama_model = _mk2.replace("models/", "").replace("-Q4_K_M", "").replace(".gguf", "")
        except Exception:
            pass
    if llama_alive and not llama_model:
        try:
            import re as _re2
            _log = open("/data/local/tmp/llama_server.log", "r", errors="ignore").read()[-6000:]
            _mm = _re2.findall(r"models/([A-Za-z0-9._\-]+)\.gguf", _log)
            if _mm:
                llama_model = _mm[-1].replace("-Q4_K_M", "").replace(".gguf", "")
        except Exception:
            pass
    _backend = str(cfg.get("llm_backend", "mimo"))
    brain = {
        "backend": _backend,
        "llama_alive": llama_alive,
        "model": llama_model or ("Qwen3-1.7B" if llama_alive else ""),
        "vision": llama_vision,
    }

    # ---- 全部功能清单（服务台展示一切；v6 起可点：jump 跳转 / sheet 弹层 / toggle 开关）----
    def _feat(k, on, note="", act=None, **extra):
        d = {"k": k, "s": ("on" if on else "off"), "note": note, "act": act}
        d.update(extra)
        return d

    net_hint = "免费·需网络"
    _greet_on = bool(cfg.get("greet_enabled", True))
    _night_on = bool(cfg.get("night_enabled", True))
    _poke_on = bool(cfg.get("poke_enabled", True))
    _chat_on = bool(cfg.get("c2c_enabled", True)) or bool(cfg.get("group_enabled", True))
    _paused = bool(st.get("bubbles_paused", False))
    features = [
        _feat("🧠 " + ("本地大模型" if _backend == "local" else "云大脑"),
              (llama_alive if _backend == "local" else bool(cfg.get("mimo_key"))),
              (brain["model"] or "未运行") if _backend == "local" else "MiMo",
              "jump", target="settings:brainGrp"),
        _feat("👁 看图说话", llama_vision,
              "发图片给它看" if llama_vision else "装视觉模型后可用",
              "sheet", detail="vision"),
        _feat("💬 聊天互动", _chat_on, "私聊·群聊都陪你", "sheet", detail="chat"),
        _feat("🗣 语音消息", True, net_hint, "jump", target="settings:voiceRow"),
        _feat("😸 表情包", sticker_n > 0, "%d 张待命" % sticker_n, "sheet", detail="sticker"),
        _feat("🐟 投喂小鱼干", True, "面板可喂", "jump", target="play:btnFeed"),
        _feat("💾 分人记忆", mem_files > 0, "%d 人档案" % len(persons), "sheet", detail="memory"),
        _feat("⏰ 定时转达", True, "一句话安排", "sheet", detail="relay"),
        _feat("🌟 QQ空间关注", True, "你发说说它会冒泡", "sheet", detail="qzone"),
        _feat("🌤 天气感知", weather_age >= 0 and weather_age < 7200, net_hint,
              "sheet", detail="weather"),
        _feat("🎂 节日&早晚安", _greet_on, "自动冒泡", "sheet", detail="greet"),
        _feat("🌙 深夜免打扰", _night_on,
              "%d:00~%d:00 安静" % (int(float(cfg.get("night_start", 0))),
                                    int(float(cfg.get("night_end", 8)))),
              "toggle", tkey="night_enabled", ton=_night_on, detail="night"),
        _feat("👉 戳一戳回应", _poke_on, "连戳会炸毛",
              "toggle", tkey="poke_enabled", ton=_poke_on),
        _feat("🫧 主动找你聊天", not _paused, "今日 %d 条" % int(st.get("bubble_today", 0)),
              "toggle", via="action", akey="toggle_bubbles", ton=not _paused),
        _feat("🔁 断线补偿 · 🧹 自愈 · 📔 日记", agent_age < 60, "它自己会照顾自己", None),
    ]

    return {
        "ok": True,
        "now": now,
        "time": time.strftime("%m-%d %H:%M:%S"),
        "overall": overall,
        "brain": brain,
        "features": features,
        "engine_online": engine_online,
        "engine_age": engine_age,
        "napcat": napcat,
        "comps": comps,
        "settable_str": {
            "local_model": str(cfg.get("local_model", "qwen2.5-vl-3b")),
            "llm_backend": str(cfg.get("llm_backend", "local")),
            "mimo_base": str(cfg.get("mimo_base", "")),
            "mimo_model": str(cfg.get("mimo_model", "")),
            "mimo_key_masked": (("%s…%s" % ((cfg.get("mimo_key") or "")[:6], (cfg.get("mimo_key") or "")[-4:]))
                                if len(str(cfg.get("mimo_key") or "")) > 12 else ("已配置" if cfg.get("mimo_key") else "未配置")),
            "city": str(cfg.get("city", "北京")),
        },
        "recent": recent,
        "said": said_list(60),
        "stickers": sticker_list(),
        "persons": persons[:12],
        "diary": [{"date": e.get("date", ""), "text": (e.get("text") or "")[:120]}
                  for e in sorted(load_json(DIARY_PATH, {"entries": []}).get("entries", []),
                                  key=lambda e: e.get("ts", 0))[-5:]],
        "settable": {
            "max_bubbles_per_day": int(float(cfg.get("max_bubbles_per_day", 20))),
            "voice_prob": float(cfg.get("voice_prob", 0.5)),
            "sticker_prob": float(cfg.get("sticker_prob", 0.22)),
            "sticker_caption_prob": float(cfg.get("sticker_caption_prob", 0.35)),
            "interject_prob": float(cfg.get("interject_prob", 0.3)),
            "bubble_min_min": int(float(cfg.get("bubble_min_min", 18))),
            "bubble_max_min": int(float(cfg.get("bubble_max_min", 55))),
        },
        "env": {
            "schedule": st.get("schedule", ""),
            "weather": st.get("weather", ""),
            "weather_age": int(st.get("weather_age", -1)),
            "bubbles_paused": bool(st.get("bubbles_paused", False)),
            "last_bubble_age": int(st.get("last_bubble_age", -1)),
            "bubble_today": int(st.get("bubble_today", 0)),
            "max_bubbles": int(cfg.get("max_bubbles_per_day", 20)),
        },
        "stats": {
            "uptime": int(st.get("uptime", 0)),
            "rss_kb": int(st.get("rss_kb", 0)),
            "facts_total": int(st.get("facts_total", 0)),
            "mood": int(st.get("mood", 80)),
            "intimacy": int(st.get("intimacy", 10)),
            "intimacy_level": st.get("intimacy_level", "熟人了"),
            "events": int(st.get("events", 0)),
            "replies": int(st.get("replies", 0)),
            "bubbles": int(st.get("bubbles", 0)),
            "bubble_today": int(st.get("bubble_today", 0)),
            "voices": int(st.get("voices", 0)),
            "stickers": int(st.get("stickers", 0)),
            "stickers_today": int(st.get("stickers_today", 0)),
            "feeds": int(st.get("feeds", 0)),
            "pokes": int(st.get("pokes", 0)),
            "diaries": int(st.get("diaries", 0)),
            "pokes_back": int(st.get("pokes_back", 0)),
            "tokens_today": int(st.get("tokens_today", 0)),
            "tokens_detail": tokens_detail(),
            "said_today": int(st.get("said_today", 0)),
            "person_files": int(st.get("person_files", 0)),
            "person_entries": int(st.get("person_entries", 0)),
            "started_at": st.get("started_at", ""),
            "started_ts": float(st.get("started_ts", 0) or 0),
            "now_ts": now,
            "llm_ok": llm_ok, "llm_fail": llm_fail,
            "active_users": int(st.get("active_users", 0)),
            "active_groups": int(st.get("active_groups", 0)),
            "owner_qq": st.get("owner_qq", 0),
        },
        "config": cfg_view,
        "logs": {"service": tail(SERVICE_LOG, 45), "napcat": tail(NAPCAT_LOG, 6)},
        "qr": {"fresh": qr_fresh, "age": qr_age},
    }


# ---------------- 动作 ----------------
def write_flag(name):
    os.makedirs(FLAG_DIR, exist_ok=True)
    with open(os.path.join(FLAG_DIR, name), "w") as f:
        f.write(time.strftime("%m-%d %H:%M:%S"))


def remove_flag(name):
    try:
        os.remove(os.path.join(FLAG_DIR, name))
    except Exception:
        pass


def do_action(action, req=None):
    if action == "restart_engine":
        write_flag("restart_engine")
        write_flag("restart_llama")
        return {"ok": True, "msg": "已重启小团子（大脑+引擎，约 15 秒）"}
    if action == "set_brain":
        req = req or {}
        cfg = load_json(CFG_PATH, {})
        b = str(req.get("backend", "")).strip()
        if b in ("local", "mimo"):
            cfg["llm_backend"] = b
        lm = str(req.get("local_model", "")).strip()
        if lm in ("qwen2.5-vl-3b", "qwen3-1.7b", "qwen3-0.6b", "qwen3-vl-4b"):
            cfg["local_model"] = lm
        for k in ("mimo_base", "mimo_model"):
            v = str(req.get(k, "")).strip()
            if v:
                cfg[k] = v
        key = str(req.get("mimo_key", "")).strip()
        if key:
            cfg["mimo_key"] = key
        save_json(CFG_PATH, cfg)
        write_flag("restart_llama")
        write_flag("restart_engine")
        _bn = "本地大脑" if cfg.get("llm_backend") == "local" else "云端大脑"
        return {"ok": True, "msg": "已切换为「%s」！约 15 秒后生效" % _bn}
    if action == "restart_napcat":
        write_flag("restart_napcat")
        return {"ok": True, "msg": "已通知重启 NapCat（会连带重启引擎，约 30 秒）"}
    if action == "force_bubble":
        write_flag("force_bubble")
        return {"ok": True, "msg": "已触发立即冒泡，小团子马上挑个人说话～"}
    if action == "test_voice":
        write_flag("test_voice")
        return {"ok": True, "msg": "已让它开嗓～去 QQ 听语音（平板本地合成，断网也能说）"}
    if action in ("pet_kitty", "feed_kitty", "play_kitty"):
        write_flag(action)
        return {"ok": True, "msg": "已告诉小团子，它马上就有反应（去 QQ 看看喵）"}
    if action == "toggle_bubbles":
        st = load_json(STATUS_PATH, {})
        if st.get("bubbles_paused"):
            remove_flag("pause_bubbles")
            return {"ok": True, "msg": "已恢复冒泡，小团子又会时不时主动找你啦", "paused": False}
        write_flag("pause_bubbles")
        return {"ok": True, "msg": "已暂停冒泡，小团子不会主动发消息了（你找它它还是会回）", "paused": True}
    if action == "test_mimo":
        cfg = load_json(CFG_PATH, {})
        key = (cfg.get("mimo_key") or "").strip()
        if not key:
            return {"ok": False, "msg": "未配置 MiMo Key"}
        t0 = time.time()
        try:
            payload = {"model": cfg.get("mimo_model", "mimo-v2.6-pro"),
                       "messages": [{"role": "user", "content": "只回复两个字：正常"}],
                       "max_tokens": 10, "temperature": 0}
            req = urllib.request.Request(
                cfg.get("mimo_base", "").rstrip("/") + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key},
                method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode("utf-8"))
            ms = int((time.time() - t0) * 1000)
            txt = (d["choices"][0]["message"]["content"] or "").strip()[:30]
            return {"ok": True, "msg": "MiMo 正常 · 延迟 %dms · 回复：%s" % (ms, txt or "（空）")}
        except Exception as e:
            return {"ok": False, "msg": "MiMo 调用失败：%s" % str(e)[:100]}
    return {"ok": False, "msg": "未知动作: %s" % action}


def heartbeat_worker():
    while True:
        try:
            os.makedirs(FLAG_DIR, exist_ok=True)
            with open(HEARTBEAT, "w") as f:
                f.write(str(int(time.time())))
        except Exception:
            pass
        time.sleep(5)


# ---------------- 仪表盘页面 ----------------
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>小团子 · 遥控台</title>
<style>
:root{
  --bg:#0a0f1e; --card:#111a30; --card2:#0d1526; --line:#1e2a4a;
  --txt:#e5ecff; --dim:#8ba3c7; --faint:#5b6b8c;
  --green:#34d399; --yellow:#fbbf24; --red:#f87171; --gray:#64748b;
  --blue:#60a5fa; --purple:#a78bfa; --pink:#f9a8d4;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;padding:14px;max-width:1180px;margin:0 auto;padding-bottom:40px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:14px 16px;background:linear-gradient(135deg,#1a2547,#0d1526);border:1px solid var(--line);border-radius:18px;margin-bottom:14px;position:relative;overflow:hidden}
header::after{content:"";position:absolute;right:-30px;top:-40px;width:150px;height:150px;border-radius:50%;background:radial-gradient(circle,rgba(96,165,250,.18),transparent 70%)}
header h1{font-size:21px;letter-spacing:1px;display:flex;align-items:center;gap:8px}
header h1 .cat{font-size:26px;animation:bob 2.6s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
.pill{margin-left:auto;padding:8px 17px;border-radius:999px;font-weight:700;font-size:14px;display:flex;align-items:center;gap:7px;z-index:1}
.pill .led{width:10px;height:10px;border-radius:50%;animation:blink 1.6s infinite}
.pill.ok{background:rgba(52,211,153,.14);color:var(--green);border:1px solid rgba(52,211,153,.4)}
.pill.ok .led{background:var(--green)}
.pill.warn{background:rgba(251,191,36,.14);color:var(--yellow);border:1px solid rgba(251,191,36,.4)}
.pill.warn .led{background:var(--yellow)}
.pill.bad{background:rgba(248,113,113,.14);color:var(--red);border:1px solid rgba(248,113,113,.4)}
.pill.bad .led{background:var(--red)}
@keyframes blink{50%{opacity:.35}}
.meta{width:100%;color:var(--dim);font-size:12.5px;display:flex;flex-wrap:wrap;gap:6px 18px;z-index:1}
.meta b{color:var(--txt);font-weight:600}
.helpbtn{background:var(--card2);border:1px solid var(--line);color:var(--blue);border-radius:999px;padding:7px 14px;font-size:13px;cursor:pointer;z-index:1;text-decoration:none;display:flex;align-items:center;gap:5px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:860px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:14px 16px;margin-bottom:14px}
.card h2{font-size:14px;color:var(--dim);font-weight:600;margin-bottom:10px;letter-spacing:2px;display:flex;align-items:center;gap:6px}
.card h2 .q{color:var(--faint);font-size:11px;letter-spacing:0}
.wide{grid-column:1/-1}
svg#topo{width:100%;height:auto;display:block}
.flow{stroke-dasharray:5 7;animation:flowmove 1.1s linear infinite}
@keyframes flowmove{to{stroke-dashoffset:-24}}
.node{cursor:pointer}
.node .core{transition:filter .2s}
.node:hover .core{filter:brightness(1.5)}
#node-detail{margin-top:8px;padding:12px 14px;background:var(--card2);border:1px solid var(--line);border-radius:12px;font-size:13px;color:var(--dim);line-height:1.8;min-height:38px}
#node-detail b{color:var(--txt)}
#node-detail .fix{color:var(--yellow);font-size:12.5px;display:block;margin-top:4px}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.hero-box{background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:12px}
.hero-box .k{font-size:11.5px;color:var(--faint);margin-bottom:5px}
.hero-box .v{font-size:14px;color:var(--txt);font-weight:600;line-height:1.5}
.hero-box .v small{color:var(--dim);font-weight:400}
.comp-row{display:flex;align-items:center;gap:10px;padding:9px 4px;border-bottom:1px dashed rgba(30,42,74,.7);font-size:13.5px}
.comp-row:last-child{border-bottom:none}
.comp-row .ic{font-size:19px;width:26px;text-align:center}
.comp-row .nm{width:96px;color:var(--txt);font-weight:600}
.comp-row .st{width:64px;font-size:12px;font-weight:700}
.comp-row .dt{color:var(--faint);font-size:12px;flex:1;text-align:right}
.st-online{color:var(--green)} .st-warn{color:var(--yellow)} .st-offline{color:var(--red)} .st-idle{color:var(--gray)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:1px}
.dot.online{background:var(--green)} .dot.warn{background:var(--yellow)} .dot.offline{background:var(--red)} .dot.idle{background:var(--gray)}
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
.stat-box{background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:10px 6px;text-align:center}
.stat-box .v{font-size:20px;font-weight:800;color:var(--blue)}
.stat-box .k{font-size:11.5px;color:var(--faint);margin-top:3px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:6px 13px;font-size:12.5px;color:var(--dim)}
.chip b{color:var(--txt);font-weight:600}
pre#logbox{background:#070d1a;border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:11.5px;line-height:1.65;color:#9fb4d8;max-height:300px;overflow:auto;white-space:pre-wrap;word-break:break-all;font-family:"Cascadia Code",Consolas,monospace}
.logtab{font-size:12px;color:var(--faint);margin-bottom:6px}
.logtab span{color:var(--blue)}
.btn{display:block;width:100%;margin:9px 0;padding:13px;border:none;border-radius:14px;font-size:15px;font-weight:700;cursor:pointer;color:#fff;transition:transform .1s,filter .2s;touch-action:manipulation;text-align:left;padding-left:18px}
.btn:active{transform:scale(.97)}
.btn:hover{filter:brightness(1.15)}
.btn:disabled{opacity:.45;cursor:wait}
.btn .sub{display:block;font-size:11.5px;font-weight:400;opacity:.85;margin-top:3px}
.b1{background:linear-gradient(135deg,#2563eb,#3b82f6)}
.b2{background:linear-gradient(135deg,#7c3aed,#a78bfa)}
.b3{background:linear-gradient(135deg,#059669,#34d399)}
.b4{background:linear-gradient(135deg,#d97706,#fbbf24)}
.b5{background:linear-gradient(135deg,#be185d,#f472b6)}
.hint{font-size:11.5px;color:var(--faint);line-height:1.8;margin-top:8px}
#progress{display:none;margin:10px 0;padding:12px 14px;background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.35);border-radius:12px;font-size:13px;color:var(--blue);line-height:1.6}
#progress.show{display:block}
#progress.ok{background:rgba(52,211,153,.08);border-color:rgba(52,211,153,.4);color:var(--green)}
#progress.warn{background:rgba(251,191,36,.08);border-color:rgba(251,191,36,.4);color:var(--yellow)}
.spin{display:inline-block;width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-2px;margin-right:6px}
@keyframes sp{to{transform:rotate(360deg)}}
#history{margin-top:10px;font-size:12px;color:var(--faint);line-height:1.9;max-height:110px;overflow:auto}
#history .ok{color:var(--green)} #history .fail{color:var(--red)}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);background:#1e293b;border:1px solid var(--blue);color:var(--txt);padding:11px 22px;border-radius:12px;font-size:13.5px;opacity:0;transition:all .3s;pointer-events:none;max-width:86vw;z-index:99;box-shadow:0 8px 30px rgba(0,0,0,.5)}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#qr-card img{width:min(300px,72vw);border-radius:12px;background:#fff;padding:8px;display:block;margin:0 auto}
.qr-tip{text-align:center;color:var(--yellow);font-size:12.5px;margin-top:8px}
footer{text-align:center;color:var(--faint);font-size:12px;line-height:1.9;padding:10px 0 20px}
/* 遮罩层：新手引导 + 确认弹窗共用 */
.mask{position:fixed;inset:0;background:rgba(4,8,18,.78);backdrop-filter:blur(3px);display:none;align-items:center;justify-content:center;z-index:100;padding:20px}
.mask.show{display:flex}
.dialog{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:22px;max-width:400px;width:100%;box-shadow:0 20px 60px rgba(0,0,0,.6);animation:pop .25s ease}
@keyframes pop{from{transform:scale(.9);opacity:0}to{transform:scale(1);opacity:1}}
.dialog .d-icon{font-size:40px;text-align:center;margin-bottom:8px}
.dialog .d-title{font-size:17px;font-weight:700;text-align:center;margin-bottom:10px}
.dialog .d-body{font-size:13.5px;color:var(--dim);line-height:1.9;margin-bottom:18px}
.dialog .d-body b{color:var(--txt)}
.dialog .d-btns{display:flex;gap:10px}
.dialog .d-btns .btn{margin:0;text-align:center;padding-left:13px;flex:1}
.guide-card{display:flex;gap:12px;align-items:flex-start;background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:10px}
.guide-card .g-ic{font-size:26px}
.guide-card .g-t{font-weight:700;font-size:14px;margin-bottom:4px}
.guide-card .g-d{font-size:12.5px;color:var(--dim);line-height:1.7}
</style>
</head>
<body>
<header>
  <h1><span class="cat">🐱</span> 小团子 · 遥控台</h1>
  <div class="pill ok" id="overall"><span class="led"></span><span id="overall-txt">检测中</span></div>
  <a class="helpbtn" href="/help" target="_blank">📖 操作说明</a>
  <div class="meta">
    <span>🕐 <b id="m-time">--</b></span>
    <span>⏱ 已运行 <b id="m-uptime">--</b></span>
    <span>💾 引擎内存 <b id="m-rss">--</b></span>
    <span>QQ <b id="m-qq">--</b></span>
    <span>🧠 <b id="m-model">--</b></span>
    <span>🛡 宿主管家 <b id="m-agent">--</b></span>
    <span>📡 面板 <b id="m-panel">--</b></span>
  </div>
</header>

<div class="grid">
  <div class="card wide">
    <h2>🌟 小团子此刻 <span class="q">它现在的心情和状态</span></h2>
    <div class="hero">
      <div class="hero-box"><div class="k">正在干嘛</div><div class="v" id="h-schedule">--</div></div>
      <div class="hero-box"><div class="k">当地天气</div><div class="v" id="h-weather">--</div></div>
      <div class="hero-box"><div class="k">上次主动冒泡</div><div class="v" id="h-bubble">--</div></div>
      <div class="hero-box"><div class="k">今日冒泡</div><div class="v" id="h-today">--</div></div>
    </div>
  </div>

  <div class="card wide">
    <h2>连 接 拓 扑 <span class="q">（点一下节点，看它是干嘛的、出问题怎么办）</span></h2>
    <svg id="topo" viewBox="0 0 680 470" xmlns="http://www.w3.org/2000/svg"></svg>
    <div id="node-detail">💡 点一下上面的任意节点，这里会用大白话告诉你：它是干嘛的、灯变红/黄了该按什么。</div>
  </div>

  <div class="card">
    <h2>组 件 状 态 <span class="q">（全绿=一切都好，不用管）</span></h2>
    <div id="comp-list"></div>
  </div>

  <div class="card">
    <h2>运 行 统 计</h2>
    <div class="stat-grid" id="stat-grid"></div>
  </div>

  <div class="card">
    <h2>设 置 摘 要 <span class="q">（这些是小团子的性格参数，看看就好）</span></h2>
    <div class="chips" id="cfg-chips"></div>
  </div>

  <div class="card">
    <h2>实 时 日 志 <span class="q">（小团子刚说了什么，5 秒自动刷新）</span></h2>
    <div class="logtab">引擎日志 · 最近 <span>45</span> 行</div>
    <pre id="logbox">加载中…</pre>
  </div>

  <div class="card">
    <h2>🎮 手 动 操 作 <span class="q">（都有二次确认，按错了取消就行）</span></h2>
    <button class="btn b3" id="btn-bubble">🐾 让它现在冒个泡<span class="sub">等不及它主动找你？点一下，它马上挑个人说话</span></button>
    <button class="btn b5" id="btn-pause">⏸ 暂停冒泡<span class="sub">想清静一阵（比如开会）：它不再主动发消息，但你找它还是会回</span></button>
    <button class="btn b1" id="btn-engine">🔄 重启小团子引擎<span class="sub">它不理人、反应迟钝、说胡话时用 · 约10秒 · 聊天记录不丢</span></button>
    <button class="btn b2" id="btn-napcat">🤖 重启 NapCat（QQ通道）<span class="sub">QQ掉线、消息发不出去时用 · 约30秒 · 面板会闪断一下（自动恢复）</span></button>
    <button class="btn b4" id="btn-mimo">🧠 测试 MiMo 大脑<span class="sub">检查它的思考能力还在不在，1秒出结果</span></button>
    <div id="progress"></div>
    <div id="history"></div>
    <div class="hint">
      💡 看不懂某个灯是什么意思？点上面的拓扑图节点，有大白话解释。<br>
      📖 第一次用？先看看右上角「操作说明」，30 秒上手。
    </div>
  </div>

  <div class="card wide" id="qr-card" style="display:none">
    <h2>登 录 二 维 码</h2>
    <img id="qr-img" alt="二维码">
    <div class="qr-tip">⚠️ NapCat 当前离线——用手机 QQ 扫码重新登录小团子（不会扫码？看「操作说明」）</div>
  </div>
</div>

<footer>
  🛡 小团子全自动运行，平时完全不用管它——出事小保姆会自动修。<br>
  所有按钮都有二次确认 · 本页面 5 秒自动刷新 · 数据都存在平板本地
</footer>

<!-- 新手引导（第一次打开时出现） -->
<div class="mask" id="guide-mask">
  <div class="dialog">
    <div class="d-icon">🐱</div>
    <div class="d-title">欢迎来到小团子遥控台</div>
    <div class="d-body" style="margin-bottom:14px">
      <div class="guide-card"><div class="g-ic">👀</div><div><div class="g-t">这是「体检报告」</div><div class="g-d">页面上的灯：<b style="color:var(--green)">绿=正常</b>、<b style="color:var(--yellow)">黄=歇一会</b>、<b style="color:var(--red)">红=出问题了</b>、灰=待命。全绿就什么都不用管。</div></div></div>
      <div class="guide-card"><div class="g-ic">🎮</div><div><div class="g-t">这也是「遥控器」</div><div class="g-d">下面的按钮能让小团子重启、冒泡、安静一会。每个按钮都有二次确认，按错了点取消就好。</div></div></div>
      <div class="guide-card"><div class="g-ic">🛡</div><div><div class="g-t">它有「小保姆」守护</div><div class="g-d">引擎或面板出事会自动拉起来，QQ 掉线也会尝试自愈。你只管撸猫 🐾</div></div></div>
    </div>
    <div class="d-btns"><button class="btn b3" id="guide-ok" style="text-align:center">太好啦，我知道了</button></div>
  </div>
</div>

<!-- 通用确认弹窗 -->
<div class="mask" id="modal-mask">
  <div class="dialog">
    <div class="d-icon" id="modal-icon">⚠️</div>
    <div class="d-title" id="modal-title"></div>
    <div class="d-body" id="modal-body"></div>
    <div class="d-btns">
      <button class="btn b4" id="modal-cancel">取消</button>
      <button class="btn b1" id="modal-ok">确认</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
var STATUS_COLOR = {online:"#34d399", warn:"#fbbf24", offline:"#f87171", idle:"#64748b"};
var STATUS_TXT = {online:"正常", warn:"注意", offline:"故障", idle:"待命"};
var RING = ["napcat","ws","mimo","tts","weather","sticker","memory","agent"];
var lastLogLen = -1;
var LAST = null;          // 最新一份 status（轮询等待用）
var opBusy = false;

function el(id){return document.getElementById(id)}
function esc(s){return (s==null?"":String(s)).replace(/[&<>]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;"}[c]})}
function toast(msg){var t=el("toast");t.textContent=msg;t.classList.add("show");clearTimeout(t._tm);t._tm=setTimeout(function(){t.classList.remove("show")},3600)}
function compById(list,id){for(var i=0;i<list.length;i++)if(list[i].id===id)return list[i];return null}
function fmtAge(sec){sec=parseInt(sec);if(sec<0)return"从未";if(sec<60)return sec+" 秒前";if(sec<3600)return Math.floor(sec/60)+" 分钟前";return Math.floor(sec/3600)+" 小时前"}
function fmtDur(sec){sec=parseInt(sec)||0;return Math.floor(sec/3600)+"小时"+Math.floor(sec%3600/60)+"分"}

/* ---------- 弹窗 ---------- */
function askConfirm(icon,title,body){
  return new Promise(function(resolve){
    el("modal-icon").textContent=icon; el("modal-title").textContent=title; el("modal-body").innerHTML=body;
    el("modal-mask").classList.add("show");
    el("modal-ok").onclick=function(){el("modal-mask").classList.remove("show");resolve(true)};
    el("modal-cancel").onclick=function(){el("modal-mask").classList.remove("show");resolve(false)};
  });
}

/* ---------- 拓扑图 ---------- */
function drawTopo(comps){
  var svg = el("topo");
  var cx=340, cy=228, R=162;
  var html = "<defs><radialGradient id='cg'><stop offset='0%' stop-color='#1c2a4a'/><stop offset='100%' stop-color='#0d1526'/></radialGradient></defs>";
  for(var i=0;i<RING.length;i++){
    var a = -Math.PI/2 + i*2*Math.PI/RING.length;
    var x = cx + R*Math.cos(a), y = cy + R*Math.sin(a);
    var c = compById(comps, RING[i]);
    var col = STATUS_COLOR[c?c.status:"idle"];
    html += "<line x1='"+cx+"' y1='"+cy+"' x2='"+x+"' y2='"+y+"' stroke='"+col+"' stroke-width='2' class='flow' opacity='.75'/>";
  }
  var wsC = compById(comps,"ws");
  if(wsC){
    var nap = RING.indexOf("napcat");
    var a2 = -Math.PI/2 + nap*2*Math.PI/RING.length;
    var mx = cx + R*0.5*Math.cos(a2), my = cy + R*0.5*Math.sin(a2);
    html += "<g><title>WS长连接："+esc(wsC.detail)+"</title><circle cx='"+mx+"' cy='"+my+"' r='13' fill='#0d1526' stroke='"+STATUS_COLOR[wsC.status]+"' stroke-width='2'/>"
          + "<text x='"+mx+"' y='"+(my+4)+"' text-anchor='middle' font-size='10' fill='"+STATUS_COLOR[wsC.status]+"'>WS</text></g>";
  }
  var eng = compById(comps,"engine");
  var ecol = STATUS_COLOR[eng?eng.status:"idle"];
  html += "<g class='node' data-id='engine'>"
    + "<title>小团子引擎："+esc(eng?eng.detail:"")+"</title>"
    + "<circle cx='"+cx+"' cy='"+cy+"' r='58' fill='none' stroke='"+ecol+"' opacity='.28'>"
    + "<animate attributeName='r' values='52;64;52' dur='2.4s' repeatCount='indefinite'/>"
    + "<animate attributeName='opacity' values='.3;.08;.3' dur='2.4s' repeatCount='indefinite'/></circle>"
    + "<circle class='core' cx='"+cx+"' cy='"+cy+"' r='46' fill='url(#cg)' stroke='"+ecol+"' stroke-width='3'/>"
    + "<text x='"+cx+"' y='"+(cy+9)+"' text-anchor='middle' font-size='34'>🐱</text>"
    + "<text x='"+cx+"' y='"+(cy+66)+"' text-anchor='middle' font-size='14' font-weight='700' fill='#e5ecff'>小团子引擎</text></g>";
  for(var i=0;i<RING.length;i++){
    var a = -Math.PI/2 + i*2*Math.PI/RING.length;
    var x = cx + R*Math.cos(a), y = cy + R*Math.sin(a);
    var c = compById(comps, RING[i]);
    var col = STATUS_COLOR[c?c.status:"idle"];
    html += "<g class='node' data-id='"+(c?c.id:"")+"' transform='translate("+x+","+y+")'>"
      + "<title>"+(c?esc(c.name):"")+"："+esc(c?c.detail:"")+"</title>"
      + "<circle class='halo' r='41' fill='none' stroke='"+col+"' opacity='.2'/>"
      + "<circle class='core' r='33' fill='#131a2e' stroke='"+col+"' stroke-width='2.5'/>"
      + "<text y='8' text-anchor='middle' font-size='24'>"+(c?c.icon:"❓")+"</text>"
      + "<text y='54' text-anchor='middle' font-size='12.5' fill='#8ba3c7'>"+(c?c.name:"")+"</text>"
      + "<circle cx='25' cy='-25' r='6.5' fill='"+col+"'/></g>";
  }
  svg.innerHTML = html;
  var nodes = svg.querySelectorAll(".node");
  for(var k=0;k<nodes.length;k++){
    nodes[k].addEventListener("click", function(){
      var c = compById(comps, this.getAttribute("data-id"));
      var d = el("node-detail");
      if(!c){d.innerHTML="（无数据）";return}
      d.innerHTML = "<b>"+c.icon+" "+esc(c.name)+"</b>　<b class='st-"+c.status+"'><span class='dot "+c.status+"'></span>"+STATUS_TXT[c.status]+"</b><br>"
        + "👉 " + esc(c.hint_plain) + "<br>📊 " + esc(c.detail)
        + (c.status!=="online" ? "<span class='fix'>🔧 " + esc(c.hint_fix) + "</span>" : "");
    });
  }
}

/* ---------- 主渲染 ---------- */
function render(d){
  LAST = d;
  var ot = el("overall");
  ot.className = "pill " + d.overall;
  el("overall-txt").textContent = d.overall==="ok"?"一切正常":(d.overall==="warn"?"部分降级":"出问题了");
  el("m-time").textContent = d.time;
  el("m-uptime").textContent = fmtDur(d.stats.uptime);
  el("m-rss").textContent = d.stats.rss_kb ? Math.round(d.stats.rss_kb/1024) + " MB" : "--";
  el("m-qq").textContent = d.napcat.nickname ? (d.napcat.nickname+"("+d.napcat.user_id+")") : "离线";
  el("m-model").textContent = d.config["模型"] || "--";
  var ag = compById(d.comps,"agent");
  el("m-agent").textContent = ag ? STATUS_TXT[ag.status] : "--";
  el("m-agent").className = ag ? "st-"+ag.status : "";
  el("m-panel").textContent = "在线";
  el("m-panel").className = "st-online";

  // 小团子此刻
  el("h-schedule").innerHTML = esc(d.env.schedule || "--");
  el("h-weather").innerHTML = d.env.weather ? esc(d.env.weather) + (d.env.weather_age>=0?" <small>"+fmtAge(d.env.weather_age)+"更新</small>":"") : "获取中…";
  el("h-bubble").textContent = fmtAge(d.env.last_bubble_age);
  el("h-today").innerHTML = d.env.bubble_today + " <small>/ 上限 " + d.env.max_bubbles + (d.env.bubbles_paused?" · 已暂停":"") + "</small>";

  drawTopo(d.comps);
  var rows = "";
  for(var i=0;i<d.comps.length;i++){
    var c = d.comps[i];
    rows += "<div class='comp-row'><span class='ic'>"+c.icon+"</span><span class='nm'>"+esc(c.name)+"</span>"
      + "<span class='st st-"+c.status+"'><span class='dot "+c.status+"'></span>"+STATUS_TXT[c.status]+"</span>"
      + "<span class='dt'>"+esc(c.detail)+"</span></div>";
  }
  el("comp-list").innerHTML = rows;

  var s = d.stats;
  var boxes = [
    [s.bubble_today, "今日冒泡"], [s.replies, "累计回复"], [s.voices, "语音条数"],
    [s.stickers, "表情包数"], [s.feeds, "被投喂次数"], [s.events, "收到事件"],
    [s.active_users, "活跃好友"], [s.active_groups, "活跃群聊"], [s.llm_ok+" / "+s.llm_fail, "大脑 成/败"]
  ];
  var g = "";
  for(var j=0;j<boxes.length;j++) g += "<div class='stat-box'><div class='v'>"+esc(boxes[j][0])+"</div><div class='k'>"+boxes[j][1]+"</div></div>";
  el("stat-grid").innerHTML = g;

  var ch = "";
  for(var k in d.config) ch += "<span class='chip'>"+esc(k)+" <b>"+esc(d.config[k])+"</b></span>";
  el("cfg-chips").innerHTML = ch;

  var logs = d.logs.service.join("\\n");
  if(logs.length !== lastLogLen){
    el("logbox").textContent = logs + (d.logs.napcat.length? "\\n—— NapCat ——\\n"+d.logs.napcat.join("\\n") : "");
    el("logbox").scrollTop = el("logbox").scrollHeight;
    lastLogLen = logs.length;
  }

  if(d.qr.fresh){ el("qr-card").style.display=""; el("qr-img").src = "/qr/napcat_qr.png?_t="+Date.now(); }
  else el("qr-card").style.display="none";

  // 暂停按钮文案跟随状态
  var pb = el("btn-pause");
  if(d.env.bubbles_paused){ pb.innerHTML = "▶️ 恢复冒泡<span class='sub'>现在它又会时不时主动找你聊天了</span>"; }
  else { pb.innerHTML = "⏸ 暂停冒泡<span class='sub'>想清静一阵（比如开会）：它不再主动发消息，但你找它还是会回</span>"; }
}

function refresh(){
  fetch("/api/status").then(function(r){return r.json()}).then(render).catch(function(){});
}
refresh();
setInterval(refresh, 5000);

/* ---------- 操作：进度可视化 + 结果轮询 ---------- */
function showProgress(html, cls){
  var p = el("progress");
  p.innerHTML = html;
  p.className = "show" + (cls?(" "+cls):"");
}
function hideProgress(){ el("progress").className = ""; }

function waitFor(checkFn, timeoutSec, onTick){
  return new Promise(function(resolve){
    var t0 = Date.now();
    var tm = setInterval(function(){
      var sec = (Date.now()-t0)/1000;
      if(onTick) onTick(sec);
      var ok = false;
      try{ ok = checkFn(); }catch(e){}
      if(ok){ clearInterval(tm); resolve(true); }
      else if(sec > timeoutSec){ clearInterval(tm); resolve(false); }
    }, 1500);
  });
}

function addHistory(label, ok, detail){
  var arr = [];
  try{ arr = JSON.parse(localStorage.getItem("tuanzi_ops")||"[]"); }catch(e){}
  arr.unshift({t:new Date().toLocaleTimeString("zh-CN",{hour12:false}), label:label, ok:ok, detail:detail||""});
  arr = arr.slice(0,8);
  try{ localStorage.setItem("tuanzi_ops", JSON.stringify(arr)); }catch(e){}
  renderHistory();
}
function renderHistory(){
  var arr = [];
  try{ arr = JSON.parse(localStorage.getItem("tuanzi_ops")||"[]"); }catch(e){}
  if(!arr.length){ el("history").innerHTML = ""; return; }
  var h = "<div style='color:var(--dim);margin-bottom:2px'>📋 最近操作：</div>";
  for(var i=0;i<arr.length;i++){
    h += "<div><span class='"+(arr[i].ok?"ok":"fail")+"'>"+(arr[i].ok?"✅":"❌")+"</span> "+esc(arr[i].t)+" "+esc(arr[i].label)+(arr[i].detail?"（"+esc(arr[i].detail)+"）":"")+"</div>";
  }
  el("history").innerHTML = h;
}
renderHistory();

function runAction(name){
  if(opBusy) return;
  var cfgs = {
    force_bubble: {btn:"btn-bubble", label:"立即冒泡",
      icon:"🐾", title:"让小团子现在冒个泡？",
      body:"它平时 18~55 分钟主动冒一次泡。<br>点确认后，它会<b>马上挑一个人</b>（可能是你也可能是群）主动说句话。<br>等不及就看它表演 ✨"},
    restart_engine: {btn:"btn-engine", label:"重启引擎",
      icon:"🔄", title:"重启小团子引擎？",
      body:"相当于让它<b>打个盹再醒来</b>：<br>· 大概 10 秒，期间它暂时不理人（正常）<br>· <b>聊天记录不会丢</b><br>· 适用于：不理人、反应迟钝、说胡话"},
    restart_napcat: {btn:"btn-napcat", label:"重启NapCat",
      icon:"🤖", title:"重启 NapCat（QQ 通道）？",
      body:"这个会比较久，<b>大概 30 秒</b>：<br>· 它会<b>连带重启引擎</b>（两个一起醒）<br>· <b>本页面会闪断一下</b>，是正常的，别关，等它自己回来<br>· 如果 QQ 要求重新登录，页面底部会出现<b>二维码</b>，用手机 QQ 扫一下<br>· 适用于：QQ 掉线、消息发不出去"},
    test_mimo: {btn:"btn-mimo", label:"测试MiMo",
      icon:"🧠", title:"测试 MiMo 大脑？",
      body:"给它的思考能力发一条<b>很小的测试消息</b>，1 秒左右出结果。<br>不影响聊天，也不花什么额度。"},
    toggle_bubbles: {btn:"btn-pause", label:"暂停/恢复冒泡",
      icon:null, title:null, body:null}
  };
  var cfg = cfgs[name];
  if(!cfg) return;

  function fire(){
    opBusy = true;
    el(cfg.btn).disabled = true;
    showProgress("<span class='spin'></span>正在" + cfg.label + "，请稍等…");
    fetch("/api/do", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({action:name})})
      .then(function(r){return r.json()})
      .then(function(res){
        if(!res.ok){
          showProgress("❌ " + esc(res.msg), "warn");
          addHistory(cfg.label, false, res.msg);
          opBusy = false; el(cfg.btn).disabled = false;
          return;
        }
        // 依据动作类型等待「真的生效」
        if(name === "force_bubble"){
          var n0 = LAST ? LAST.stats.bubbles : 0;
          waitFor(function(){ return LAST && LAST.stats.bubbles > n0; }, 45, function(sec){
            showProgress("<span class='spin'></span>小团子正在挑人说话… 已等待 " + Math.round(sec) + " 秒");
          }).then(function(ok){
            finish(ok, cfg, ok ? "它已经冒泡了" : "等超时了，可能它在深夜免打扰时段，刷新看看日志");
          });
        } else if(name === "restart_engine"){
          waitFor(function(){ return LAST && LAST.engine_online; }, 60, function(sec){
            showProgress("<span class='spin'></span>引擎重启中… 已等待 " + Math.round(sec) + " 秒（约10秒好）");
          }).then(function(ok){
            finish(ok, cfg, ok ? "引擎已恢复上线" : "等超时了，稍等几秒刷新页面看看");
          });
        } else if(name === "restart_napcat"){
          showProgress("<span class='spin'></span>NapCat 重启中… 页面可能会闪断，是正常的，别关页面（已等待 0 秒）");
          var t0 = Date.now();
          waitFor(function(){ return LAST && LAST.napcat.online && LAST.engine_online; }, 150, function(sec){
            showProgress("<span class='spin'></span>NapCat 重启中… 页面闪断是正常的，别关（已等待 " + Math.round(sec) + " 秒，约30秒好）");
          }).then(function(ok){
            finish(ok, cfg, ok ? "NapCat 和引擎都回来了（耗时 " + Math.round((Date.now()-t0)/1000) + " 秒）" : "等超时了，刷新页面看看；如需登录请扫页面底部二维码");
          });
        } else {
          // test_mimo / toggle_bubbles：POST 响应即结果
          finish(true, cfg, res.msg);
        }
      })
      .catch(function(){
        showProgress("⚠️ 请求没发出去（页面闪断中？会自动重试，稍等）", "warn");
        addHistory(cfg.label, false, "请求失败");
        opBusy = false; el(cfg.btn).disabled = false;
      });
  }

  function finish(ok, cfg, detail){
    showProgress((ok?"✅ ":"⚠️ ") + esc(detail), ok?"ok":"warn");
    toast((ok?"✅ ":"⚠️ ") + detail);
    addHistory(cfg.label, ok, detail);
    opBusy = false; el(cfg.btn).disabled = false;
  }

  if(name === "toggle_bubbles"){
    fire();   // 暂停/恢复无破坏性，不确认
    return;
  }
  askConfirm(cfg.icon, cfg.title, cfg.body).then(function(ok){ if(ok) fire(); });
}

el("btn-bubble").onclick = function(){ runAction("force_bubble") };
el("btn-pause").onclick = function(){ runAction("toggle_bubbles") };
el("btn-engine").onclick = function(){ runAction("restart_engine") };
el("btn-napcat").onclick = function(){ runAction("restart_napcat") };
el("btn-mimo").onclick = function(){ runAction("test_mimo") };

/* ---------- 新手引导（仅第一次） ---------- */
try{
  if(!localStorage.getItem("tuanzi_guided")){
    el("guide-mask").classList.add("show");
  }
}catch(e){}
el("guide-ok").onclick = function(){
  el("guide-mask").classList.remove("show");
  try{ localStorage.setItem("tuanzi_guided","1"); }catch(e){}
};
</script>
</body>
</html>
"""

# ---------------- 新手操作说明页 ----------------
HELP_FALLBACK = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>操作说明</title></head>
<body style="background:#0a0f1e;color:#e5ecff;font-family:sans-serif;padding:30px;line-height:2">
<h1>📖 操作说明还没部署</h1>
<p>请把 help.html 放到 /root/tuanzi/ 目录，然后刷新本页。</p>
<p><a href="/" style="color:#60a5fa">← 返回控制台</a></p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        try:
            path = self.path.split("?")[0]
            if path == "/" or path == "/index.html":
                self._send(200, load_dashboard(), "text/html; charset=utf-8")
            elif path == "/help":
                try:
                    with open(HELP_PATH, "r", encoding="utf-8") as f:
                        self._send(200, f.read(), "text/html; charset=utf-8")
                except Exception:
                    self._send(200, HELP_FALLBACK, "text/html; charset=utf-8")
            elif path == "/api/status":
                self._send(200, json.dumps(collect(), ensure_ascii=False))
            elif path == "/api/detail":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                kk = (qs.get("k") or [""])[0]
                self._send(200, json.dumps(detail_api(kk), ensure_ascii=False))
            elif path.startswith("/stk/"):
                # 表情包静态图：白名单校验文件名，防路径穿越
                name = os.path.basename(path[len("/stk/"):])
                if not name or not re.match(r"^[A-Za-z0-9_.\-]+\.(jpg|png|gif)$", name, re.I):
                    self._send(404, '{"ok":false}')
                    return
                fp = os.path.join(PICS_DIR, name)
                if not os.path.isfile(fp):
                    self._send(404, '{"ok":false}')
                    return
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                ctype = {"jpg": "image/jpeg", "png": "image/png",
                         "gif": "image/gif"}.get(ext, "application/octet-stream")
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ctype)
            elif path == "/pic":
                qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                name = os.path.basename((qs.get("f") or [""])[0])
                ext = os.path.splitext(name)[1].lower()
                if not name or ext not in IMG_EXTS:
                    self._send(404, '{"ok":false}')
                    return
                fp = os.path.join(PICS_DIR, name)
                if not os.path.isfile(fp):
                    self._send(404, '{"ok":false}')
                    return
                ctype = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                         "png": "image/png", "gif": "image/gif"}[ext[1:]]
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ctype)
            elif path.startswith("/qr/"):
                name = os.path.basename(path[4:])
                if name not in ("napcat_qr.png", "qr.html"):
                    self._send(403, '{"ok":false}')
                    return
                fp = os.path.join(QR_DIR, name)
                if not os.path.exists(fp):
                    self._send(404, '{"ok":false}')
                    return
                ctype = "text/html" if name.endswith(".html") else "image/png"
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ctype)
            else:
                self._send(404, '{"ok":false,"msg":"not found"}')
        except Exception as e:
            log("GET err: %s" % e)
            try:
                self._send(500, '{"ok":false}')
            except Exception:
                pass

    def do_POST(self):
        def api_set(key, value):
            SETTABLE = {
                "max_bubbles_per_day": (int, 5, 50, "每天主动找你次数"),
                "voice_prob": (float, 0, 1, "发语音概率"),
                "sticker_prob": (float, 0, 1, "聊天带表情包概率"),
                "sticker_caption_prob": (float, 0, 1, "表情包自动配文概率"),
                "interject_prob": (float, 0, 1, "群里插嘴概率"),
                "bubble_min_min": (int, 10, 120, "冒泡最小间隔(分钟)"),
                "bubble_max_min": (int, 30, 240, "冒泡最大间隔(分钟)"),
            }
            SETTABLE_STR = {
                "mimo_base": (200, "模型地址"),
                "mimo_model": (60, "模型名"),
                "mimo_key": (120, "API Key"),
                "city": (20, "城市"),
            }
            # v6：功能一览里的开关（布尔）
            SETTABLE_BOOL = {
                "night_enabled": "深夜免打扰",
                "poke_enabled": "戳一戳回应",
                "weather_enabled": "天气感知",
                "greet_enabled": "节日&早晚安",
                "c2c_enabled": "私聊聊天",
                "group_enabled": "群聊互动",
            }
            # v6：早/晚安冒泡整点（0-23）
            SETTABLE_HOUR = {
                "greet_morning_hour": "早安时间",
                "greet_evening_hour": "晚安时间",
            }
            if key in SETTABLE_BOOL:
                b = value
                if isinstance(value, str):
                    b = value.strip().lower() in ("1", "true", "on", "yes")
                with CFG_WLOCK:
                    c = load_json(CFG_PATH, {})
                    c[key] = bool(b)
                    save_json(CFG_PATH, c)
                write_flag("restart_engine")
                return {"ok": True, "msg": "已%s「%s」，小团子重启让它生效（约10秒）"
                        % ("打开" if b else "关闭", SETTABLE_BOOL[key])}
            if key in SETTABLE_HOUR:
                try:
                    hh = int(float(value))
                except Exception:
                    return {"ok": False, "msg": "时间格式不对（要 0~23 的整点）"}
                if hh < 0 or hh > 23:
                    return {"ok": False, "msg": "时间要在 0 ~ 23 点之间哦"}
                with CFG_WLOCK:
                    c = load_json(CFG_PATH, {})
                    c[key] = hh
                    save_json(CFG_PATH, c)
                write_flag("restart_engine")
                return {"ok": True, "msg": "已把「%s」设为 %d 点，小团子重启生效（约10秒）"
                        % (SETTABLE_HOUR[key], hh)}
            if key in SETTABLE_STR:
                maxlen, label = SETTABLE_STR[key]
                v = str(value or "").strip()
                if len(v) > maxlen:
                    return {"ok": False, "msg": "%s 太长啦（最多 %d 字）" % (label, maxlen)}
                if not v and key != "mimo_key":
                    return {"ok": False, "msg": "%s 不能是空的" % label}
                with CFG_WLOCK:
                    c = load_json(CFG_PATH, {})
                    c[key] = v
                    save_json(CFG_PATH, c)
                write_flag("restart_engine")
                if key == "mimo_key" and not v:
                    return {"ok": True, "msg": "已清空 API Key（小团子将无法思考聊天，谨慎！）小团子重启中"}
                return {"ok": True, "msg": "已保存 %s，小团子正在重启让它生效（约10秒）" % label}
            if key not in SETTABLE:
                return {"ok": False, "msg": "这个设置项不认识"}
            typ, lo, hi, label = SETTABLE[key]
            try:
                v = typ(float(value))
            except Exception:
                return {"ok": False, "msg": "数值格式不对"}
            if v < lo or v > hi:
                return {"ok": False, "msg": "%s 需要在 %s ~ %s 之间" % (label, lo, hi)}
            with CFG_WLOCK:
                c = load_json(CFG_PATH, {})
                c[key] = v
                save_json(CFG_PATH, c)
            write_flag("restart_engine")
            return {"ok": True, "msg": "已保存 %s=%s，小团子正在重启让设置生效（约10秒）" % (label, v)}
        try:
            path = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            req = json.loads(raw.decode("utf-8"))
            if path == "/api/do":
                res = do_action(req.get("action", ""), req)
                log("action %s -> %s" % (req.get("action"), res.get("msg")))
                self._send(200, json.dumps(res, ensure_ascii=False))
            elif path == "/api/set":
                res = api_set(str(req.get("key", "")), req.get("value", ""))
                log("set %s -> %s" % (req.get("key"), res.get("msg")))
                self._send(200, json.dumps(res, ensure_ascii=False))
            elif path == "/api/sticker_save":
                res = sticker_save(str(req.get("file", "")), str(req.get("emotion", "")),
                                   str(req.get("note", "")))
                log("sticker save %s -> %s" % (req.get("file"), res.get("msg")))
                self._send(200, json.dumps(res, ensure_ascii=False))
            elif path == "/api/sticker_del":
                res = sticker_del(str(req.get("file", "")))
                log("sticker del %s -> %s" % (req.get("file"), res.get("msg")))
                self._send(200, json.dumps(res, ensure_ascii=False))
            elif path == "/api/sticker_upload":
                res = sticker_upload(str(req.get("name", "")), str(req.get("b64", "")))
                log("sticker upload %s -> %s" % (req.get("name"), res.get("msg")))
                self._send(200, json.dumps(res, ensure_ascii=False))
            else:
                self._send(404, '{"ok":false}')
        except Exception as e:
            log("POST err: %s" % e)
            try:
                self._send(500, '{"ok":false,"msg":"%s"}' % e)
            except Exception:
                pass


def main():
    os.makedirs(FLAG_DIR, exist_ok=True)
    threading.Thread(target=heartbeat_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log("panel server v4 start on :%d" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
