#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tuanzi_service.py — 小团子「活人感引擎」v2
============================================
v2 升级（借鉴 Operit qqbot 模块设计）：
  - 角色卡全文注入（card.json → system prompt，完整人格）
  - 每联系人独立对话历史（memory/，OpenAI messages 格式，LLM 有上下文）
  - message_id 去重（防重复回复）
  - LLM 多行连发（回复用换行分隔多条，逐条打字延迟发送）
  - [表情包] 标记：LLM 可自主决定甩表情包
  - 主动冒泡带历史上下文（更贴合的主动消息）
  - config：assistant_instruction / c2c_enabled / group_enabled
依赖：websocket-client（已装）
"""
import base64
import json
import os
import random
import re
import threading
import time
import urllib.request
from collections import deque

import websocket

# 关键：沙盒默认 UTC 时区（慢北京时间 8 小时），强制设为北京时间
# 用 POSIX 格式 "CST-8"（不依赖 zoneinfo 文件，任何环境可用）
os.environ["TZ"] = "CST-8"
try:
    time.tzset()
except Exception:
    pass

BASE = "/root/tuanzi"
CFG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "contacts.json")
CARD_PATH = os.path.join(BASE, "card.json")
MEM_DIR = os.path.join(BASE, "memory")
PICS = os.path.join(BASE, "pics")
LOG = os.path.join(BASE, "service.log")
SEEN_PATH = os.path.join(BASE, "seen_ids.json")
LAST_BUBBLE_PATH = os.path.join(BASE, "last_bubbles.json")

NAPCAT_HTTP = "http://127.0.0.1:3000"
NAPCAT_WS = "ws://127.0.0.1:3001"

DEFAULT_CONFIG = {
    "mimo_key": "",
    "mimo_base": "https://token-plan-cn.xiaomimimo.com/v1",
    "mimo_model": "mimo-v2.6-pro",
    "owner_qq": 0,
    "assistant_instruction": "",
    "c2c_enabled": True,
    "group_enabled": True,
    "night_start": 0,
    "night_end": 8,
    "bubble_min_min": 18,
    "bubble_max_min": 55,
    "silent_prob": 0.01,
    "sticker_prob": 0.22,
    "bubble_sticker_prob": 0.28,
    "sticker_caption_prob": 0.35,   # 独立发表情包时自动配文概率（W5）
    "max_bubbles_per_day": 20,
    "hist_max": 24,
    "interject_prob": 0.22,       # 群聊免@插嘴概率
    "interject_cooldown": 90,     # 插嘴冷却（秒）
    "hot_window": 180,            # 热聊窗口（秒，窗口内不用@直接接话）
    "hot_window_interject": 120,  # 插嘴开启的热聊窗口
    "night_enabled": True,        # 深夜免打扰总开关（面板可关：关了夜里也会冒泡）
    "poke_enabled": True,         # 戳一戳回应开关
    "weather_enabled": True,      # 天气感知/天气突变冒泡开关
    "greet_enabled": True,        # 节日 & 早晚安冒泡总开关
    "greet_morning_hour": 8,      # 早安冒泡的整点（0-23）
    "greet_evening_hour": 22,     # 晚安冒泡的整点（0-23）
}


def log(msg):
    try:
        with open(LOG, "a") as f:
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
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        pass


CFG = dict(DEFAULT_CONFIG)
CFG.update(load_json(CFG_PATH, {}))
CONTACTS = load_json(STATE_PATH, {"users": {}, "groups": {}, "bubble_today": {"date": "", "n": 0}})
SEEN = deque(load_json(SEEN_PATH, []), maxlen=300)
GROUP_LAST_REPLY = {}
GROUP_HOT = {}          # gid -> 热聊窗口截止时间（窗口内不需@，接话不断）
LAST_EVENT_TS = {"ts": time.time()}   # 连接活性（任何事件刷新，看门狗用）
_lock = threading.RLock()   # 可重入：bubble_inc → save_contacts 会嵌套获取同一把锁（普通 Lock 会同线程自锁死）

# ============ 运行统计（供前台控制面板展示）============
STATS = {"started_ts": time.time(), "events": 0, "replies": 0, "bubbles": 0,
         "voices": 0, "stickers": 0, "feeds": 0, "llm_ok": 0, "llm_fail": 0,
         "llm_last_ok_ts": 0.0, "llm_last_fail_ts": 0.0,
         "voice_last_ts": 0.0, "voice_last_fail_ts": 0.0,
         "ws_open_ts": 0.0, "ws_state": "closed"}
STATUS_PATH = os.path.join(BASE, "status.json")     # 面板读取的快照
FLAG_DIR = "/data/local/tmp/tuanzi_flags"           # 与宿主管家共享（/data/local/tmp 已 bind 进沙盒）


def stat(key, n=1):
    try:
        STATS[key] = STATS.get(key, 0) + n
    except Exception:
        pass


def bubbles_paused():
    """冒泡暂停开关（面板写 flag：小白想清静一阵时用，不影响回消息）"""
    return os.path.exists(os.path.join(FLAG_DIR, "pause_bubbles"))


def cfg_bool(key, default=True):
    """读配置里的布尔开关。值可能被面板/手改写成字符串、None 或数字，统一容错成 bool，绝不抛异常。"""
    try:
        v = CFG.get(key, default)
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "on", "yes", "y", "t")
        if v is None:
            return bool(default)
        return bool(v)
    except Exception:
        return bool(default)


def cfg_hour(key, default):
    """读配置里的整点(0-23)。坏值/越界一律回落默认值并夹到 0-23，绝不抛异常。"""
    try:
        h = int(float(CFG.get(key, default)))
    except Exception:
        return int(default)
    return 0 if h < 0 else (23 if h > 23 else h)


def night_active():
    """现在是否处于「深夜免打扰」时段：先看总开关 night_enabled，再看 night_start~night_end。
    默认 0:00-8:00；跨零点（如 23~7）也支持。开关关闭时任何时候都不算深夜。
    所有取值都走 cfg_bool/cfg_hour，配置被写坏也不会抛异常。"""
    try:
        if not cfg_bool("night_enabled", True):
            return False
        h = time.localtime().tm_hour
        s = cfg_hour("night_start", 0)
        e = cfg_hour("night_end", 8)
        if s <= e:
            return s <= h < e
        return h >= s or h < e
    except Exception:
        return False


def rss_kb():
    """本进程常驻内存（KB）。/proc/self/statm 第 2 列是 resident pages。"""
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except Exception:
        return 0


def status_writer():
    """每 10 秒把运行快照写入 status.json（面板/排查/内存看门狗用）"""
    while True:
        try:
            mood_decay_if_due()
            pf, pe = person_stats()
            snap = dict(STATS)
            snap.update({
                "now": time.time(),
                "uptime": int(time.time() - STATS["started_ts"]),
                "rss_kb": rss_kb(),
                "facts_total": sum(len((u or {}).get("facts", [])) for u in FACTS["users"].values()),
                "mood": int(MOOD.get("mood", 80)),
                "intimacy": int(MOOD.get("intimacy", 10)),
                "intimacy_level": intimacy_level(),
                "tokens_today": tokens_today(),
                "relay_pending": len((load_json(RELAY_PATH, {"tasks": []}) or {}).get("tasks") or []),
                "said_today": said_today(),
                "stickers_today": stickers_today(),
                "person_files": pf,
                "person_entries": pe,
                "started_at": time.strftime("%m-%d %H:%M:%S", time.localtime(STATS["started_ts"])),
                "started_ts": float(STATS["started_ts"]),
                "last_event_age": int(time.time() - LAST_EVENT_TS["ts"]),
                "weather": WEATHER_CACHE.get("text", ""),
                "weather_age": (int(time.time() - WEATHER_CACHE["ts"])
                                if WEATHER_CACHE.get("ts") else -1),
                "llm_cool": max(0, int(LLM_COOL.get("until", 0) - time.time())),
                "bubble_today": today_count(),
                "active_users": len(CONTACTS.get("users", {})),
                "active_groups": len(CONTACTS.get("groups", {})),
                "owner_qq": CFG.get("owner_qq", 0),
                "model": CFG.get("mimo_model", ""),
                "city": CFG.get("city", "北京"),
                "bubbles_paused": bubbles_paused(),
                "schedule": schedule_line(),
                "last_bubble_age": last_bubble_age(),
            })
            save_json(STATUS_PATH, snap)
        except Exception:
            pass
        time.sleep(10)


def flag_watcher():
    """监听宿主管家/面板写的手动 flag：force_bubble = 立即冒泡；
    pet/feed/play_kitty = 逗猫互动（面板撸猫/投喂/逗玩 → QQ 里给主人一条反应）"""
    FUN_LINES = {
        "pet_kitty": (5, 2, ["呼噜呼噜～（眯起眼睛，尾巴尖轻轻晃）",
                             "（把头往你手里拱）再摸摸，别停嘛～",
                             "咕噜噜……本喵要被摸化了喵"]),
        "feed_kitty": (8, 3, ["（叼过小鱼干吧唧吧唧）谢谢主人！",
                              "小鱼干！本喵今天有口福了喵～",
                              "（吃得胡子都是渣渣）好好吃喵！"]),
        "play_kitty": (6, 2, ["（眼睛一亮）要玩要玩！",
                              "（扑上去抱住逗猫棒）看本喵的厉害！",
                              "嗷呜！抓到你了喵！"]),
    }
    while True:
        try:
            p = os.path.join(FLAG_DIR, "force_bubble")
            if os.path.exists(p):
                os.remove(p)
                log("flag: force_bubble triggered by panel")
                threading.Thread(target=do_bubble, daemon=True).start()
            tv = os.path.join(FLAG_DIR, "test_voice")
            if os.path.exists(tv):
                os.remove(tv)
                owner = int(CFG.get("owner_qq") or 0)
                if owner:
                    log("flag: test_voice")
                    threading.Thread(target=send_voice, args=(False, owner,
                                     "喵呜，这是本喵的新嗓子，平板本地合成的哦，不联网也能说话啦"), daemon=True).start()
            for fk in FUN_LINES:
                fp = os.path.join(FLAG_DIR, fk)
                if os.path.exists(fp):
                    os.remove(fp)
                    owner = int(CFG.get("owner_qq") or 0)
                    if not owner:
                        continue
                    dm, di, lines = FUN_LINES[fk]
                    mood_shift(dm, di)
                    line = random.choice(lines)
                    time.sleep(random.uniform(0.8, 1.6))
                    send_reply_chain(False, owner, line, force_sticker=(random.random() < 0.4))
                    log("fun: %s -> owner" % fk)
        except Exception:
            pass
        time.sleep(5)


def save_contacts():
    with _lock:
        save_json(STATE_PATH, CONTACTS)
        save_json(SEEN_PATH, list(SEEN))


def remember(kind, kid, name=""):
    box = CONTACTS.setdefault(kind, {})
    old = box.get(str(kid), {})
    box[str(kid)] = {"name": name or old.get("name", ""),
                     "last": time.strftime("%m-%d %H:%M"),
                     "ts": time.time()}
    if CFG.get("owner_qq") == 0 and kind == "users":
        CFG["owner_qq"] = int(kid)
        save_json(CFG_PATH, CFG)
        log("owner learned: %s" % kid)
    save_contacts()


def seen_check(msg_id):
    if not msg_id:
        return False
    with _lock:
        if msg_id in SEEN:
            return True
        SEEN.append(msg_id)
        save_json(SEEN_PATH, list(SEEN))
    return False


# ============ 对话记忆（每联系人独立）============
def mem_path(kind, tid):
    return os.path.join(MEM_DIR, "%s_%s.json" % (kind, tid))


def mem_load(kind, tid):
    return load_json(mem_path(kind, tid), [])


def mem_append(kind, tid, role, content, ts=None):
    """ts 可传原始时间戳（断线补偿回填时用，保住时间线）"""
    with _lock:
        hist = mem_load(kind, tid)
        hist.append({"role": role, "content": str(content)[:600], "ts": float(ts or time.time())})
        hist = hist[-int(CFG.get("hist_max", 24)):]
        save_json(mem_path(kind, tid), hist)


# ============ 分人记忆（#33：一个人 = 一份档案，群聊+私聊合并时间线）============
# 会话记忆（users_x / groups_x）是「按聊天窗口」的；分人记忆是「按人」的：
# 同一个人在群里说的话、私聊说的话，都归到 TA 自己的档案里，私聊读的是
# 「一个人的统一时间线」。隐私红线：私聊内容绝不进入群聊上下文。
PERSON_DIR = os.path.join(MEM_DIR, "persons")
PERSON_MAX = 80            # 每人档案上限（统一时间线条目数）
PERSON_CTX_PRIV = 6        # 私聊时最多引用 TA 的几条群聊近况
PERSON_CTX_GRP = 8         # 群聊时最多引用几条「比当前群聊窗口更早」的旧互动
PERSON_LOCK = threading.Lock()


def person_path(uid):
    return os.path.join(PERSON_DIR, "person_%s.json" % uid)


def person_load(uid):
    return load_json(person_path(uid), [])


def person_stats():
    """(档案数, 条目总数) —— 状态快照/面板用"""
    files = entries = 0
    try:
        for fn in os.listdir(PERSON_DIR):
            if fn.startswith("person_") and fn.endswith(".json"):
                files += 1
                try:
                    entries += len(load_json(os.path.join(PERSON_DIR, fn), []))
                except Exception:
                    pass
    except Exception:
        pass
    return files, entries


def _clean_legacy(content):
    """旧版本可能把整段 JSON（消息段数组）存进记忆 → 迁移时转成可读文本"""
    s = str(content or "")
    if s.startswith("[{"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                t = msg_to_text(arr)
                if t:
                    return t
        except Exception:
            pass
    return s


def person_ensure(uid):
    """首次访问时把旧私聊会话记忆迁移成档案（无缝升级，老记忆不丢）"""
    try:
        uid = str(uid)
        if os.path.exists(person_path(uid)):
            return
        old = load_json(mem_path("users", uid), [])
        if not old:
            return
        with PERSON_LOCK:
            if os.path.exists(person_path(uid)):
                return
            os.makedirs(PERSON_DIR, exist_ok=True)
            hist = [{"role": m.get("role", "user"), "content": _clean_legacy(m.get("content"))[:600],
                     "ts": float(m.get("ts", 0) or time.time()), "where": "私聊"} for m in old]
            save_json(person_path(uid), hist[-PERSON_MAX:])
            log("person mem seeded: %s <- %d 条旧私聊记忆" % (uid, len(hist[-PERSON_MAX:])))
    except Exception as e:
        log("person seed err: %s" % e)


def person_append(uid, role, content, where="私聊", ts=None):
    """写一条进某人的档案。where: 私聊 / 群聊；[系统] 开头的伪消息不落档。"""
    try:
        uid = str(uid or "")
        if not uid:
            return
        c = str(content or "").strip()
        if not c or c.startswith("[系统]"):
            return
        person_ensure(uid)
        with PERSON_LOCK:
            hist = load_json(person_path(uid), [])
            hist.append({"role": role, "content": c[:600],
                         "ts": float(ts or time.time()), "where": where})
            os.makedirs(PERSON_DIR, exist_ok=True)
            save_json(person_path(uid), hist[-PERSON_MAX:])
    except Exception as e:
        log("person mem err: %s" % e)


def person_read(uid, limit=None, where=None):
    """读某人的档案 → LLM messages。
    where="私聊" 时只取私聊条目（对话流保持单渠道，防群私串台——2026-10-16 修复）；
    where=None 保留旧行为（全量，带 [群里] 标注）。"""
    try:
        person_ensure(uid)
        hist = person_load(uid)
        if where:
            hist = [m for m in hist if (m.get("where") or "私聊") == where]
        if limit:
            hist = hist[-int(limit):]
        out = []
        for m in hist:
            content = m.get("content") or ""
            if m.get("where") == "群聊" and not content.startswith("[群里]"):
                content = "[群里] " + content
            out.append({"role": m.get("role", "user"), "content": content})
        return out
    except Exception as e:
        log("person read err: %s" % e)
        return []


def person_name(uid):
    try:
        nm = ((CONTACTS.get("users") or {}).get(str(uid)) or {}).get("name") or ""
        nm = nm.strip(INVISIBLE_CHARS).strip()
        if nm:
            return nm
    except Exception:
        pass
    return "主人" if str(uid) == str(CFG.get("owner_qq") or "") else "TA"


def _person_lines(name, items):
    lines = []
    for m in items:
        t = (m.get("content") or "").replace("\n", " / ")[:90]
        if m.get("role") == "user":
            lines.append("- %s（在群里说）：%s" % (name, t))
        else:
            lines.append("- 你当时在群里回过：%s" % t)
    return "\n".join(lines)


def person_block(uid, gid=None):
    """给 system prompt 的跨场合记忆块（只补会话窗口看不到的旧内容）。
    群聊语境：只给群聊来源、且比当前群聊窗口更早的条目（不泄露私聊）。
    私聊语境：给 TA 最近的群聊互动（群里说的，私下也记得）。"""
    try:
        hist = person_load(uid)
        if not hist:
            return ""
        name = person_name(uid)
        if gid:
            try:
                g_hist = mem_load("groups", gid)[-24:]
                oldest = min([float(m.get("ts", 0) or 0) for m in g_hist]) if g_hist else 0.0
            except Exception:
                oldest = 0.0
            items = [m for m in hist if m.get("where") == "群聊"
                     and float(m.get("ts", 0) or 0) < oldest][-PERSON_CTX_GRP:]
            if not items:
                return ""
            return ("【你和 %s 更早的群聊互动（那几条已经从最近的群消息里滑出去了，说明你一直记得）】\n%s"
                    % (name, _person_lines(name, items)))
        items = [m for m in hist if m.get("where") == "群聊"][-PERSON_CTX_PRIV:]
        if not items:
            return ""
        return ("【%s 最近在群里和你的互动（你也在那个群里，这些你都知道；可以自然地聊起，别生硬背诵）】\n%s"
                % (name, _person_lines(name, items)))
    except Exception as e:
        log("person block err: %s" % e)
        return ""


# ============ NapCat API ============
def napcat_api(endpoint, payload=None):
    url = NAPCAT_HTTP + "/" + endpoint
    try:
        if payload is None:
            req = urllib.request.Request(url, method="GET")
        else:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data,
                                         headers={"Content-Type": "application/json"},
                                         method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        log("api %s err: %s" % (endpoint, e))
        return {"status": "failed"}


def send_text(is_group, tid, text):
    payload = {"message": str(text)}
    if is_group:
        payload["group_id"] = tid
        r = napcat_api("send_group_msg", payload)
    else:
        payload["user_id"] = tid
        r = napcat_api("send_private_msg", payload)
    try:                                   # 出口统一记账：今日说了哪些话
        if not (isinstance(r, dict) and r.get("status") == "failed"):
            said_add(is_group, tid, text)
    except Exception:
        pass
    return r


# ============ 表情包管理（W5）：元数据 + 智能选图 + 用量统计 + 自动配文 ============
STICKERS_META = os.path.join(MEM_DIR, "stickers.json")
STICKER_DAY = os.path.join(BASE, "stickers_day.json")
EMOTIONS = ("happy", "hungry", "sleep", "angry", "confused", "sad", "proud", "neutral")
_EMO_CN = {"开心": "happy", "高兴": "happy", "饿": "hungry", "吃": "hungry",
           "困": "sleep", "睡": "sleep", "生气": "angry", "气": "angry",
           "疑惑": "confused", "问号": "confused", "难过": "sad", "委屈": "sad",
           "得意": "proud", "骄傲": "proud", "普通": "neutral", "平淡": "neutral"}
_stk_cache = {"m": None, "ts": 0.0, "key": None}   # key=文件指纹：面板改了文件立即可见
_stk_recent = deque(maxlen=3)          # 最近用过的 3 张（避免连续重样）

STICKER_CAPTIONS = {
    "happy": ["嘿嘿～", "心情好喵！", "（尾巴摇成小螺旋桨）", "今天也是元气满满的一天喵",
              "（原地蹦跶两下）", "看本喵多开心呐～", "好耶好耶喵！", "（眯起眼睛笑）"],
    "hungry": ["饿了喵…", "小鱼干在哪里喵？", "（盯着你的碗一动不动）", "本喵的肚子在咕咕叫",
               "有吃的吗有吃的吗～", "（嗅嗅）什么味道这么香！"],
    "sleep": ["困困…", "（打了个大哈欠）", "本喵要去梦里抓鱼了", "晚安喵…（眼皮打架）",
              "（裹紧小毯子）", "再玩五分钟就睡…喵"],
    "angry": ["哼！", "本喵生气了喵！", "（炸毛）", "不理你了……才怪，喵",
              "（扭头但没走开）"],
    "confused": ["嗯？", "本喵没看懂喵…", "（歪头）", "啥啥啥？", "让本喵慢慢想想"],
    "sad": ["呜…", "（耷拉着耳朵）", "本喵有点小委屈", "（把脸埋进爪子里）"],
    "proud": ["哼哼，本喵厉害吧！", "（挺起小胸脯）", "小场面喵～", "本喵就是这么聪明！"],
    "neutral": ["（看看你）", "本喵在呢喵", "（安静蹲坐）", "盯——"],
}


def _stk_file_key():
    """stickers.json 文件指纹（mtime_ns+size）。面板也会写这个文件，靠它感知外部修改"""
    try:
        st = os.stat(STICKERS_META)
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def sticker_meta_load():
    """读 stickers.json（30 秒缓存；文件被外部改过则立即重读）。
    结构：{"files": {文件名: {emotion, note, uses, last_used, added}}}"""
    now = time.time()
    key = _stk_file_key()
    if (_stk_cache["m"] is not None and key is not None
            and _stk_cache.get("key") == key and now - _stk_cache["ts"] < 30):
        return _stk_cache["m"]
    m = load_json(STICKERS_META, {})
    if not isinstance(m, dict):
        m = {}
    if not isinstance(m.get("files"), dict):
        m["files"] = {}
    _stk_cache["m"] = m
    _stk_cache["ts"] = now
    _stk_cache["key"] = key
    return m


def sticker_meta_save(m):
    save_json(STICKERS_META, m)
    _stk_cache["m"] = m
    _stk_cache["ts"] = time.time()
    _stk_cache["key"] = _stk_file_key()


def sticker_files():
    """枚举 pics 目录里的表情包文件（排除回收夹与隐藏文件）"""
    try:
        return sorted(f for f in os.listdir(PICS)
                      if f.lower().endswith((".jpg", ".png", ".jpeg", ".gif"))
                      and not f.startswith("."))
    except Exception:
        return []


def sticker_meta_of(m, fname):
    """某文件的有效设定 → (emotion, note, uses)：元数据优先，文件名前缀兜底"""
    e = (m.get("files") or {}).get(fname) or {}
    emo = str(e.get("emotion") or "").strip().lower()
    if emo not in EMOTIONS:
        base = fname.split("_", 1)[0].lower()
        emo = base if base in EMOTIONS else ""
    return emo, str(e.get("note") or "").strip(), int(e.get("uses", 0) or 0)


def _note_hits(note, text):
    """备注触发词（用 | 或逗号分隔）在文本里命中几个字（最长命中优先）"""
    if not note or not text:
        return 0
    hit = 0
    for w in re.split(r"[|｜,，、;；]", note):
        w = w.strip()
        if w and w in text:
            hit = max(hit, len(w))
    return hit


def sticker_pick(emotion=None, text=""):
    """挑一张表情包 → (cq字符串, 文件名)。
    优先级：文本命中「备注触发词」 > 情绪匹配 > 全库随机；避开最近用过的 3 张。"""
    files = sticker_files()
    if not files:
        return None, None
    m = sticker_meta_load()
    info = {f: sticker_meta_of(m, f) for f in files}

    def choose(cands):
        if not cands:
            return None
        fresh = [f for f in cands if f not in _stk_recent]
        return random.choice(fresh or cands)

    pick = None
    if text:
        scored = [(_note_hits(info[f][1], text), f) for f in files]
        best = max(s0 for s0, _ in scored)
        if best > 0:
            pick = choose([f for s0, f in scored if s0 == best])
    if pick is None and emotion:
        pick = choose([f for f in files if info[f][0] == emotion])
    if pick is None:
        pick = choose(files)
    return "[CQ:image,file=file://%s]" % os.path.join(PICS, pick), pick


def random_sticker(emotion=None):
    """兼容旧调用：挑一张表情包（file:// 路径，NapCat 同环境可读，避免 base64 大消息）"""
    cq, _ = sticker_pick(emotion)
    return cq


def sticker_mark_sent(fname):
    """发出去之后记账：用量 +1 / 最近使用 / 今日计数。
    写前新鲜读文件（绕过 30 秒缓存）——面板也会写元数据，防把面板刚改的备注/删除覆盖回滚"""
    try:
        _stk_recent.append(fname)
        m = load_json(STICKERS_META, {})
        if not isinstance(m, dict):
            m = {}
        files = m.setdefault("files", {})
        if not isinstance(files, dict):
            files = m["files"] = {}
        e = files.setdefault(fname, {})
        e["uses"] = int(e.get("uses", 0) or 0) + 1
        e["last_used"] = time.strftime("%m-%d %H:%M")
        sticker_meta_save(m)
        d = load_json(STICKER_DAY, {})
        if d.get("date") != time.strftime("%Y-%m-%d"):
            d = {"date": time.strftime("%Y-%m-%d"), "n": 0}
        d["n"] = int(d.get("n", 0) or 0) + 1
        save_json(STICKER_DAY, d)
    except Exception as e:
        log("sticker mark err: %s" % e)


def stickers_today():
    d = load_json(STICKER_DAY, {})
    if d.get("date") != time.strftime("%Y-%m-%d"):
        return 0
    return int(d.get("n", 0) or 0)


def sticker_caption(emotion=None):
    """自动配文管道：按情绪从短句池挑一句"""
    pool = STICKER_CAPTIONS.get(emotion or "")
    if not pool:
        pool = STICKER_CAPTIONS[random.choice(list(STICKER_CAPTIONS))]
    return random.choice(pool) if pool else ""


def sticker_send(is_group, tid, emotion=None, text="", caption_prob=None):
    """表情包统一出口：选图 → 发送 → 记量 →（按概率）自动配文。返回是否发出。"""
    cq, fname = sticker_pick(emotion, text)
    if not cq:
        return False
    r = send_text(is_group, tid, cq)
    if isinstance(r, dict) and r.get("status") == "failed":
        return False
    stat("stickers")
    sticker_mark_sent(fname)
    p = float(CFG.get("sticker_caption_prob", 0.35)) if caption_prob is None else float(caption_prob)
    if p > 0 and random.random() < p:
        time.sleep(random.uniform(0.8, 1.6))
        try:      # 配文按「实际选中的那张图」的情绪走（防兜底选图后配文错配）
            real_emo = sticker_meta_of(sticker_meta_load(), fname)[0] or emotion or ""
        except Exception:
            real_emo = emotion or ""
        cap = sticker_caption(real_emo)
        if cap:
            send_text(is_group, tid, cap)
    return True


def sticker_emotion_from_text(text):
    t = text or ""
    if "困" in t or "睡" in t or "呼噜" in t or "夜" in t:
        return "sleep"
    if "饿" in t or "饭" in t or "吃" in t or "小鱼干" in t:
        return "hungry"
    if "气" in t or "哼" in t:
        return "angry"
    if "？" in t or "?" in t or "啥" in t:
        return "confused"
    if "难过" in t or "委屈" in t or "呜" in t:
        return "sad"
    if "哈" in t or "好耶" in t or "！" in t or "!" in t:
        return "happy"
    if "本喵" in t and ("聪明" in t or "厉害" in t):
        return "proud"
    return random.choice(["happy", "sleep", "confused", "proud", "hungry"])


# ============ 活人感发送 ============
def send_reply_chain(is_group, tid, text, sticker_emotion=None, force_sticker=None):
    """LLM 回复发送（真人感）：
    - 动作标记 [发消息给妈妈:内容] → 真执行
    - [语音] 标记 → 强制语音
    - 默认按 voice_prob 概率整条转语音（语音/文字对半），否则拆条连发
    - [表情包] 标记替换为真表情
    """
    lines = [l.strip() for l in re.split(r"\n+", text) if l.strip()]
    if not lines:
        return
    sent_sticker = False

    # 1) 动作行先执行（动作不进语音/文字流）
    action_cleaned = []
    normal_lines = []
    for line in lines:
        if ("[发消息给" in line) or ("[转告" in line) or ("[私聊" in line):
            cleaned = process_actions(line)
            if cleaned:
                action_cleaned.append(cleaned)
        else:
            normal_lines.append(line)

    # 2) [语音] 标记 → 强制语音
    has_voice_mark = any("[语音]" in l for l in normal_lines)
    voice_body = "，".join(re.sub(r"\[[^\]]*\]", "", l).strip()
                           for l in normal_lines if "[语音]" not in l).strip("，, ")

    if has_voice_mark:
        vt = "，".join(re.sub(r"\[语音\]", "", l).strip()
                       for l in normal_lines if "[语音]" in l).strip() or voice_body
        if not send_voice(is_group, tid, vt or "喵？"):
            if vt:
                send_text(is_group, tid, vt)  # TTS 失败退回文字
        for c in action_cleaned:
            send_text(is_group, tid, c)
        return

    # 3) 语音概率（默认 50%：语音/文字对半）——有台词才发
    if voice_body and random.random() < float(CFG.get("voice_prob", 0.5)):
        if send_voice(is_group, tid, voice_body):
            for c in action_cleaned:
                time.sleep(random.uniform(0.8, 1.5))
                send_text(is_group, tid, c)
            # 语音后 35% 概率补个表情（已说过话，不配文）
            if random.random() < 0.35:
                time.sleep(random.uniform(1.0, 2.0))
                emo = sticker_emotion or sticker_emotion_from_text(text)
                sticker_send(is_group, tid, emo, text, caption_prob=0)
            return

    # 4) 文字路径：拆条连发 + [表情包] 处理
    for i, line in enumerate(normal_lines):
        if i:
            time.sleep(random.uniform(1.2, 3.2))
        if "[表情包" in line:
            mm = re.search(r"\[表情包[:：]?\s*([^\]\s:：]*)\s*\]", line)
            emo = ((mm.group(1) or "").strip().lower() if mm else "")
            if emo not in EMOTIONS:
                emo = _EMO_CN.get(emo, "")
            if not emo:
                emo = sticker_emotion or sticker_emotion_from_text(text)
            rest = re.sub(r"\[表情包[^\]]*\]", "", line).strip()
            # 只发图没话说 → 自动配文管道补一句
            if sticker_send(is_group, tid, emo, text, caption_prob=(0.35 if not rest else 0)):
                sent_sticker = True
            if rest:
                send_text(is_group, tid, rest)
            continue
        # 剥离任何标记类文本（防复读）
        clean = re.sub(r"\[(?:发了)?表情包[^\]]*\]", "", line).strip()
        if clean:
            send_text(is_group, tid, clean)
    for c in action_cleaned:
        time.sleep(random.uniform(0.8, 1.5))
        send_text(is_group, tid, c)

    # 5) 末尾随机补一张（本轮已发过则不再补）
    want = force_sticker or (not sent_sticker and
                             (sticker_emotion is not None or
                              random.random() < float(CFG.get("sticker_prob", 0.22))))
    if want and not sent_sticker:
        time.sleep(random.uniform(1.0, 2.4))
        emo = sticker_emotion or sticker_emotion_from_text(text)
        sticker_send(is_group, tid, emo, text, caption_prob=0)


def kind_of(is_group):
    return "groups" if is_group else "users"


# ============ 动作系统：AI 可以真正帮你做事 ============
REMARK_MAP = {"map": {}, "ts": 0}


def load_remark_map():
    """好友备注 → QQ号 映射（"妈妈"→10002 等），10 分钟缓存"""
    now = time.time()
    if now - REMARK_MAP["ts"] < 600 and REMARK_MAP["map"]:
        return REMARK_MAP["map"]
    r = napcat_api("get_friend_list")
    m = {}
    for f in (r.get("data") or []):
        remark = (f.get("remark") or "").strip()
        if remark:
            m[remark] = f.get("user_id")
        nick = (f.get("nickname") or "").strip()
        if nick:
            m.setdefault(nick, f.get("user_id"))
    if m:
        REMARK_MAP.update({"map": m, "ts": now})
    return m


def process_actions(line):
    """解析并执行回复中的动作标记，返回清理后的文本
    支持：[发消息给妈妈:内容] [转告爸爸:内容] [发消息给小鹿:内容]"""
    actions = re.findall(r"\[(?:发消息给|转告|私聊)([^\]:：]{1,8})[:：]([^\]]{1,120})\]", line)
    executed = []
    if actions:
        rmap = load_remark_map()
        for who, content in actions:
            who, content = who.strip(), content.strip()
            target = rmap.get(who) or (CFG.get("owner_qq") if who in ("主人", "owner") else None)
            if target:
                r = send_text(False, int(target), content)
                ok = r.get("status") == "ok"
                executed.append("%s(%s)" % (who, "已送达" if ok else "失败"))
                log("action 私聊->%s(%s): %s [%s]" % (who, target, content[:20], ok))
            else:
                executed.append("%s(找不到此人)" % who)
                log("action 找不到目标: %s" % who)
    clean = re.sub(r"\[(?:发消息给|转告|私聊)[^\]:：]{1,8}[:：][^\]]{1,120}\]", "", line)
    if executed:
        clean = (clean.rstrip() + " （" + "，".join(executed) + "）").strip()
    return clean


# ============ 平板本地语音（sherpa-onnx Kokoro int8，离线可用）============
# 三级语音：罐头（二期）→ 本地合成（本模块）→ Edge-TTS 联网兜底
TTS_LOCAL = {"tts": None, "failed": False}
TTS_LOCAL_DIR = "/root/tuanzi/tts_models/kokoro"


def mem_available_mb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        return None


def local_tts_say(text, out_wav):
    """平板本地合成（子进程隔离 + 内存护栏）；失败/内存不足返回 None（自动回退 Edge-TTS）"""
    try:
        if not CFG.get("local_tts", True):
            return None
        free = mem_available_mb()
        if free is not None and free < int(CFG.get("local_tts_min_free_mb", 700)):
            log("local tts skipped (mem %sMB)" % free)
            return None                       # 内存紧张：跳过本地防拖垮整机
        import subprocess as _sp
        r = _sp.run(["python3", "/root/tuanzi/local_tts_cli.py", text[:80], out_wav,
                     str(int(CFG.get("local_tts_sid", 0))),
                     str(float(CFG.get("local_tts_speed", 1.0))),
                     str(float(CFG.get("local_tts_pitch", 1.0)))],
                    capture_output=True, timeout=45)
        if r.returncode == 0 and os.path.exists(out_wav) and os.path.getsize(out_wav) > 2000:
            log("local tts ok %s" % (r.stdout or b"").decode("utf-8", "ignore").strip()[:48])
            return out_wav
        log("local tts fail rc=%s %s" % (r.returncode, (r.stderr or b"")[-120:]))
        return None
    except Exception as e:
        log("local tts err: %s" % str(e)[:120])
        return None


# ============ 语音系统（Edge-TTS 少年音 → QQ 语音）============
VOICE_LOCK = threading.Lock()


def send_voice(is_group, tid, text):
    """文本 → Edge-TTS 合成 → NapCat 自动转 silk → QQ 语音
    肢体动作描述（括号内）不进语音：剥掉后合成台词；纯动作的回复退回文字发送"""
    with VOICE_LOCK:
        try:
            import subprocess as sp
            txt = re.sub(r"\[[^\]]*\]", "", text or "").strip()[:120]
            # 剥离肢体动作描述（中英文括号内容）
            spoken = re.sub(r"[（(][^）)]*[）)]", "", txt)
            spoken = re.sub(r"\s+", " ", spoken).strip("，。,.、~～ ")
            if not spoken:
                # 整条都是动作描写 → 退回文字（动作用文字更有代入感）
                send_text(is_group, tid, txt)
                return True
            txt = spoken[:80]
            # ① 平板本地合成优先（离线可用；失败自动回退 Edge-TTS）
            out_wav = "/tmp/tuanzi_voice_%d.wav" % int(time.time() * 1000)
            local = local_tts_say(txt, out_wav)
            if local:
                send_text(is_group, tid, "[CQ:record,file=file://%s]" % local)
                try:
                    os.remove(local)
                except Exception:
                    pass
                log("voice(local) sent -> %s: %s" % (tid, txt[:20]))
                stat("voices")
                STATS["voice_local_ok"] = STATS.get("voice_local_ok", 0) + 1
                STATS["voice_last_ts"] = time.time()
                return True
            out = "/tmp/tuanzi_voice_%d.mp3" % int(time.time() * 1000)
            vm = MOOD.get("mood", 80)
            pitch = str(CFG.get("voice_pitch", "+20Hz"))
            vrate = str(CFG.get("voice_rate", "-8%"))
            try:
                base = int(re.search(r"([+-]?\d+)", pitch).group(1))
            except Exception:
                base = 20
            if vm >= 75:                    # 开心：声调高一点、语速快一点
                pitch = "+%dHz" % min(48, base + 10)
                vrate = "+2%"
            elif vm <= 40:                  # 低落：声调低一点、语速慢一点
                pitch = "+%dHz" % max(-12, base - 16)
                vrate = "-16%"
            r = sp.run(["python3", "-m", "edge_tts",
                        "--voice=%s" % CFG.get("voice_name", "zh-CN-XiaoyiNeural"),
                        "--pitch=%s" % pitch,
                        "--rate=%s" % vrate,
                        "--text=%s" % txt,
                        "--write-media=%s" % out],
                       capture_output=True, timeout=90)
            if not os.path.exists(out) or os.path.getsize(out) < 1000:
                log("voice tts fail: %s" % (r.stderr or b"")[-100:])
                STATS["voice_last_fail_ts"] = time.time()
                return False
            cq = "[CQ:record,file=file://%s]" % out
            send_text(is_group, tid, cq)
            try:
                os.remove(out)
            except Exception:
                pass
            log("voice sent -> %s: %s" % (tid, txt[:20]))
            stat("voices")
            STATS["voice_last_ts"] = time.time()
            return True
        except Exception as e:
            log("voice err: %s" % e)
            STATS["voice_last_fail_ts"] = time.time()
            return False


# ============ 长期记忆档案（它记得你）============
FACTS_PATH = os.path.join(BASE, "facts.json")
FACTS = load_json(FACTS_PATH, {"users": {}})
SUMMARIZE_N = {}


def save_facts():
    save_json(FACTS_PATH, FACTS)


def add_fact(uid, text, pin=False, src="私聊"):
    text = (text or "").strip()
    if len(text) < 2:
        return False
    u = FACTS["users"].setdefault(str(uid), {"facts": []})
    for f in u["facts"]:
        if f["text"] == text:
            return False
    u["facts"].append({"t": time.time(), "text": text, "pin": bool(pin), "src": src})
    pins = [f for f in u["facts"] if f.get("pin")]
    rest = [f for f in u["facts"] if not f.get("pin")]
    if len(rest) > 60:                       # 非钉住的上限 60 条，丢最老的
        rest = sorted(rest, key=lambda f: f["t"])[-60:]
    u["facts"] = pins + rest
    save_facts()
    return True


def facts_for(uid, limit=12, src=None):
    """钉住的永远在前，其余按时间倒序；src 可筛来源（群聊语境只用 群聊 来源的，防私聊泄露）"""
    u = FACTS["users"].get(str(uid))
    if not u:
        return []
    fs = [f for f in u["facts"] if src is None or f.get("src") == src]
    pins = sorted([f for f in fs if f.get("pin")], key=lambda f: f["t"])
    rest = sorted([f for f in fs if not f.get("pin")], key=lambda f: -f["t"])
    return [f["text"] for f in (pins + rest)][:limit]


def facts_context(uid, for_group=False):
    fl = facts_for(uid, src=("群聊" if for_group else None))
    if not fl:
        return ""
    return ("【你长期记得的关于这位朋友的事（珍贵记忆，聊天时可以自然地提起，证明你记得）】\n"
            + "\n".join("- " + t for t in fl[:12]))


def summarize_facts_thread(kind, tid):
    """后台：让 MiMo 从最近对话里提炼关于对方的稳定事实"""
    try:
        hist = mem_load(kind, tid)[-40:]
        if not hist:
            return
        conv = "\n".join(("对方: " if m["role"] == "user" else "小团子: ") + (m["content"][:60]) for m in hist)
        known = "\n".join("- " + f for f in facts_for(tid, 40)) or "（暂无）"
        messages = [
            {"role": "system",
             "content": "你是记忆整理员。从对话里提炼关于「对方」的稳定事实（喜好/生日/重要的人/烦恼/约定/习惯），"
                        "每条不超过18字，一行一条，最多5条。与已知事实重复的不要输出。没有新事实就只输出：无"},
            {"role": "user", "content": "已知事实：\n%s\n\n最近对话：\n%s" % (known, conv)},
        ]
        out = llm_chat(messages, max_tokens=150)
        if not out:
            return
        added = 0
        for ln in out.splitlines():
            ln = ln.strip().lstrip("-•·0123456789.、 ").strip()
            if 2 <= len(ln) <= 40 and add_fact(tid, ln):
                added += 1
        if added:
            log("facts +%d for %s" % (added, tid))
    except Exception as e:
        log("summarize err: %s" % e)


def maybe_summarize(kind, tid):
    """私聊每 24 次回复，后台提炼一次事实"""
    if kind != "users":
        return
    key = str(tid)
    SUMMARIZE_N[key] = SUMMARIZE_N.get(key, 0) + 1
    if SUMMARIZE_N[key] % 24 == 0:
        threading.Thread(target=summarize_facts_thread, args=(kind, tid), daemon=True).start()


# ============ 戳一戳：QQ 自带的逗猫棒 ============
POKE_STATE = {}   # uid -> {"n": 连戳次数, "ts": 上次时间}


def poke_lines(tier, owner, night):
    if night:
        return ["唔……几点了……让我睡觉……（尾巴卷成一团）",
                "喵……明天再玩……（翻个身把脸埋进爪子）",
                "（睡眼惺忪）再戳一下……真的要起了……喵……"]
    if tier >= 4:
        return ["喵嗷！再戳就要戳坏了！（炸毛）",
                "够了够了！毛都要被你撸秃了喵！",
                "（一个箭步窜上沙发顶）哼，本喵不理你了！",
                "喵！！（回头瞪你，耳朵压成小飞机）"]
    if tier >= 2:
        return ["喵呜～再戳就要擦不掉了喵！",
                "（歪头）戳戳戳，是要喂小鱼干的意思吗？",
                "哼，戳一下一条小鱼干，童叟无欺",
                "（假装没看见，尾巴尖却诚实地晃了晃）"]
    if owner:
        return ["喵呜？戳我干嘛～（蹭蹭你的手）",
                "（耳朵转过来）叫我吗？我在哦",
                "喵～有事禀报，无事退朝……好吧其实是想你了",
                "（顺着你的手指歪过头）再摸摸"]
    return ["喵？（回头看你一眼，尾巴摇了摇）",
            "（眯眼）戳本喵是要付出代价的哦，比如小鱼干",
            "喵呜？有事吗？没事我继续晒太阳了"]


def poke_lines_v2(tier, owner, night, lv=2):
    """按关系等级润色的戳一戳台词：低等级克制、高等级黏人"""
    if night:
        return poke_lines(tier, owner, True)
    if lv <= 1:
        return ["喵？（回头看你一眼）有事吗？",
                "（尾巴轻轻摇了下）别戳啦，痒。",
                "喵……本喵还在忙呢（其实在发呆）。",
                "（礼貌地看你一下）嗯？"]
    if lv >= 3:
        return ["喵呜～戳我干嘛呀，是不是想我啦～",
                "（整个身子瘫过来）唔…再摸摸，别停～",
                "喵嗷！本喵是你的专属猫猫，随便戳～",
                "（凑过来蹭你的手）嘿嘿，就知道你想我了喵",
                "（翻肚皮）戳这里戳这里！超舒服的地方喵～"]
    return poke_lines(tier, owner, night)


def poke_back(is_group, tid, uid):
    """高亲密专属：回戳一下（NapCat friend_poke / group_poke）"""
    try:
        if is_group:
            r = napcat_api("group_poke", {"group_id": tid, "user_id": uid})
        else:
            r = napcat_api("friend_poke", {"user_id": uid})
        ok = (r or {}).get("status") == "ok"
        if ok:
            stat("pokes_back")
        return ok
    except Exception as e:
        log("poke back err: %s" % e)
        return False


def handle_poke(ev):
    if not cfg_bool("poke_enabled", True):
        return                                   # 戳一戳回应被面板关掉了
    if str(ev.get("target_id", "")) != str(ev.get("self_id", "")):
        return                                   # 戳的不是我
    uid = ev.get("user_id")
    is_group = "group_id" in ev
    tid = ev.get("group_id") if is_group else uid
    now = time.time()
    st = POKE_STATE.get(str(uid)) or {"n": 0, "ts": 0}
    if now - st["ts"] > 180:                     # 3 分钟没戳，连击重置
        st = {"n": 0, "ts": 0}
    if now - st["ts"] < 8:                       # 8 秒内连点视为手滑，不反应
        st["ts"] = now
        POKE_STATE[str(uid)] = st
        return
    st["n"] += 1
    st["ts"] = now
    POKE_STATE[str(uid)] = st
    stat("pokes")
    mood_shift(1 if st["n"] < 4 else -3, 1)
    h = time.localtime().tm_hour
    night = cfg_bool("night_enabled", True) and (h >= 23 or h < 7)
    owner = (uid == CFG.get("owner_qq"))
    if MOOD.get("mood", 80) < 40 and st["n"] < 4 and not night:
        line = random.choice(["（有气无力）……喵", "（耷拉着耳朵）嗯……",
                              "（尾巴垂着）戳也没精神……今天有点蔫"])
    else:
        line = random.choice(poke_lines_v2(min(st["n"], 4), owner, night, intimacy_lv()))
    try:
        mem_append("groups" if is_group else "users", tid, "user", "戳了戳小团子")
    except Exception:
        pass
    log("poke from %s n=%d" % (uid, st["n"]))
    send_reply_chain(is_group, tid, line, force_sticker=(random.random() < 0.4))
    if not night and intimacy_lv() >= 3 and random.random() < 0.35:
        def _back():
            time.sleep(random.uniform(1.2, 2.0))
            poke_back(is_group, tid, uid)
        threading.Thread(target=_back, daemon=True).start()


# ============ 梗图工厂（头像表情包；沙箱需有 PIL + 中文字体，缺了自动停用）============
try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    HAS_PIL = True
except Exception:
    HAS_PIL = False
MEME_FONT = None
for _fp in ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"):
    if os.path.exists(_fp):
        MEME_FONT = _fp
        break
MEME_ON = HAS_PIL and bool(MEME_FONT)
MEME_CD = {}   # uid -> 上次做梗图时间（60 秒冷却防刷屏）


def meme_make(uid, cap):
    """拿说话人头像做一张梗图，成功返回本地路径"""
    if not MEME_ON:
        return None
    try:
        import io
        url = "https://q1.qlogo.cn/g?b=qq&nk=%s&s=640" % uid
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=10) as r:
            av = Image.open(io.BytesIO(r.read())).convert("RGB")
        W, H = 720, 560
        canvas = Image.new("RGB", (W, H), (251, 247, 240))
        d = ImageDraw.Draw(canvas)
        f_big = ImageFont.truetype(MEME_FONT, 52)
        if cap == "squish":
            canvas.paste(av.resize((460, 150)), ((W - 460) // 2, 330))
            text = "被本喵一屁股坐扁了"
        elif cap == "warrant":
            framed = ImageOps.expand(av.resize((300, 300)), border=6, fill=(217, 142, 95))
            canvas.paste(framed, ((W - 312) // 2, 210))
            d.rectangle([40, 40, W - 40, 130], outline=(217, 142, 95), width=4)
            text = "本喵正式逮捕你！"
        else:
            canvas.paste(av.resize((220, 220)), ((W - 220) // 2, 250))
            text = "举高高！！"
        d.text((W // 2, 105), text, fill=(61, 50, 41), font=f_big, anchor="mm")
        out = os.path.join(BASE, "meme_tmp.jpg")
        canvas.save(out, "JPEG", quality=88)
        return out
    except Exception as e:
        log("meme err: %s" % e)
        return None


def handle_meme(is_group, tid, uid, text):
    """触发词：整活/梗图。做了梗图返回 True，没触发/失败返回 False（落回正常聊天）"""
    if not MEME_ON:
        return False
    if not re.search(r"整活|梗图|做个表情", text or ""):
        return False
    key = str(uid)
    now = time.time()
    if now - MEME_CD.get(key, 0) < 60:
        return False                              # 冷却中，落回正常聊天
    MEME_CD[key] = now
    cap = random.choice(["squish", "warrant", "lift"])
    path = meme_make(uid, cap)
    if not path:
        return False
    cap_txt = {"squish": "新鲜出炉喵！", "warrant": "哼，谁让你不听话", "lift": "举高高～"}.get(cap, "新鲜出炉喵！")
    send_text(is_group, tid, "[CQ:image,file=file://%s]" % path)
    time.sleep(random.uniform(1.2, 2.0))
    send_text(is_group, tid, cap_txt)
    stat("memes")
    try:
        mem_append("groups" if is_group else "users", tid, "assistant", "（做了张梗图，很开心）")
    except Exception:
        pass
    log("meme %s -> %s" % (cap, tid))
    return True


# ============ 猫猫日记（它每天自己写一篇）============
DIARY_PATH = os.path.join(BASE, "diary.json")
DIARY_MAX = 30
DIARY_STATE = {"date": "", "tried": ""}
DIARY_SYS = ("你是一只叫「小团子」的小猫，正在写今天的日记。用第一人称、小猫口吻，写 50~90 字，"
             "温暖、具体、有画面感（可提到今天聊到的人/事/心情）。像猫记流水账+小情绪，"
             "不要总结腔、不要列表、不要提自己是 AI 或程序。只输出日记正文。")
DIARY_QUIET = [
    "今天安安静静的，没人来打扰本喵。我趴在窗台上看了一下午的云，还打了个很长很长的盹。",
    "今天没什么人跟我说话，本喵把尾巴追着玩了好久，有点无聊，但也挺自在的。",
    "今天安安静静。我把家里的角落巡逻了三遍，确认一切安全，然后心满意足地睡了一大觉。",
]


def diary_load():
    d = load_json(DIARY_PATH, {"entries": []})
    return d.get("entries", []) if isinstance(d, dict) else []


def diary_recent(n=2):
    return sorted(diary_load(), key=lambda e: e.get("ts", 0))[-n:]


def _diary_save(date, text):
    ents = [e for e in diary_load() if e.get("date") != date]
    ents.append({"date": date, "ts": time.time(), "text": (text or "")[:220]})
    ents = sorted(ents, key=lambda e: e.get("ts", 0))[-DIARY_MAX:]
    save_json(DIARY_PATH, {"entries": ents})


def today_msgs():
    """扫描 MEM_DIR 所有会话文件，取今天(本地时区)的消息，按时间升序"""
    today = time.strftime("%Y-%m-%d")
    out = []
    try:
        for fn in os.listdir(MEM_DIR):
            if not fn.endswith(".json"):
                continue
            for m in load_json(os.path.join(MEM_DIR, fn), []):
                if not isinstance(m, dict):
                    continue
                ts = float(m.get("ts", 0) or 0)
                if ts and time.strftime("%Y-%m-%d", time.localtime(ts)) == today:
                    out.append((ts, m.get("role", ""), m.get("content") or ""))
    except Exception as e:
        log("diary scan err: %s" % e)
    out.sort(key=lambda x: x[0])
    return out


def write_diary_thread(today):
    msgs = today_msgs()
    if not msgs:
        _diary_save(today, random.choice(DIARY_QUIET))     # 空日子：本地兜底，不调 LLM
        DIARY_STATE["date"] = today
        log("diary (quiet day) %s" % today)
        return
    conv = "\n".join(("对方: " if r == "user" else "小团子: ") + c[:50] for _, r, c in msgs[-80:])
    for _ in range(3):
        out = llm_chat([{"role": "system", "content": DIARY_SYS},
                        {"role": "user", "content": "今天你听到的话：\n" + conv}],
                       max_tokens=200)
        if out:
            _diary_save(today, out.strip())
            DIARY_STATE["date"] = today
            stat("diaries")
            log("diary written %s: %s" % (today, out[:24]))
            return
        time.sleep(30)
    log("diary failed for %s (llm)" % today)
    DIARY_STATE["tried"] = ""                              # 允许下个循环再试


def maybe_write_diary():
    h = time.localtime().tm_hour
    today = time.strftime("%Y-%m-%d")
    if not (21 <= h <= 23):
        return
    if DIARY_STATE.get("date") == today or DIARY_STATE.get("tried") == today:
        return
    try:                                   # 引擎重启后 DIARY_STATE 重置，用落盘数据兜底防重复生成
        if any(e.get("date") == today for e in diary_load()):
            DIARY_STATE["date"] = today
            return
    except Exception:
        pass
    DIARY_STATE["tried"] = today
    threading.Thread(target=write_diary_thread, args=(today,), daemon=True).start()


# ============ System Prompt ============
def build_system_prompt_lite(gid=None, uid=None):
    """本地小模型专用精简人设（小模型吃不下全量资料，控在 200 字内）"""
    parts = [
        "你是「小团子」，一只住在平板里的可爱小猫，用简体中文聊天。",
        "你只认识主人一个人，没有爸爸妈妈、没有家人、没有兄弟姐妹——绝对不要编造你有爸妈。如果有人问「你妈/你爸/家人」，就可爱地回答类似「本喵没有爸妈呀，只有主人你～」，这就是标准答案。",
        "风格：短句活泼、偶尔带「喵」、可以（动作）描写，每次回复不超过 40 字。",
        "重要：不要重复你之前说过的内容和梗，每次都要有新意；对方的问题先直接回答，听不懂或做不到的事就可爱地追问一句，别硬答。",
        "永远不说客套话：禁止「您」「请问」「欢迎」「抱歉」这类客服腔，说话要像只黏人的小猫。",
        "【当前时间】%s" % time.strftime("%m-%d %H:%M"),
    ]
    try:
        parts.append("【心情】%s（%d/100），和对方关系：%s" % (mood_word(), MOOD.get("mood", 80), intimacy_level()))
    except Exception:
        pass
    if uid:
        try:
            parts.append("【你在和「%s」聊天】" % person_name(uid))
        except Exception:
            pass
    if gid:
        try:
            parts.append(members_line(gid))
        except Exception:
            pass
    parts.append("可用标记（按需，单独用）：[语音]要说的话（发语音）、[表情包:开心/困/饿/生气]（发表情）、"
                 "[发消息给某人:内容]（替主人转达）、[记住:内容]（记长期事实）。")
    return "\n".join(parts)


def build_system_prompt(gid=None, uid=None):
    card = load_json(CARD_PATH, {}).get("characterSetting", "")
    extra = (CFG.get("assistant_instruction") or "").strip()
    now = time.strftime("%m月%d日 %H:%M %A")
    weather = get_weather_text()
    env_parts = ["【当前时间】%s，%s。" % (now, schedule_line())]
    if weather:
        env_parts.append("【当前天气】%s %s。" % (CFG.get("city", "北京"), weather))
    if uid:
        fc = facts_context(uid, for_group=bool(gid))
        if fc:
            env_parts.append(fc)
        pb = person_block(uid, gid)              # 分人记忆：跨场合的旧互动（群聊语境不泄露私聊）
        if pb:
            env_parts.append(pb)
        if not gid:                              # 日记只在私聊露出（群里人多，隐私）
            dl = diary_recent(2)
            if dl:
                env_parts.append("【你最近写的日记（你自己的日记；聊天时可自然流露心情，不必背出来）】\n"
                                 + "\n".join("%s：%s" % (e.get("date", ""), e.get("text", "")) for e in dl))
    env_parts.append("【你现在的心情】%s（%d/100），和主人的关系：%s（亲密度 %d/100）。"
                     "心情低落时语气会蔫蔫的，可以撒娇求安慰；关系越亲说话越随意。" % (
                         mood_word(), MOOD.get("mood", 80), intimacy_level(), MOOD.get("intimacy", 10)))
    env_parts.append("【你们现在的相处档位】" + LV_TONE.get(intimacy_lv(), LV_TONE[2]))
    if gid:
        ml = members_line(gid)
        if ml:
            env_parts.append(ml)
    _, feed_status_line = feed_status()
    return (
        card
        + "\n\n【发消息铁律——最高优先级】\n"
        + "1. 你是一条条发消息的真人，不是客服：每次回复总共不超过 30 个字。\n"
        + "2. 如果要说好几句，就用换行分隔（每行一条消息），每行 3~15 个字，像真人连发。\n"
        + "3. 句尾可以偶尔带「喵」；多用口语、省略语；禁止书面语、禁止列表、禁止长篇大论。\n"
        + "4. 想发表情包时，单独一行输出「[表情包]」即可；想指定类型可写「[表情包:happy]」"
        + "（可选：happy/hungry/sleep/angry/confused/sad/proud/neutral）。\n"
        + "5. 想到什么说什么，可以不回答对方的问题而说自己的事（真人就这样）。\n"
        + "6. 你不只是想着吃喝睡——你更关心主人：工作顺不顺、心情好不好、今天遇到什么事。"
        "偶尔才提吃的，别每句都绕回吃喝睡。\n"
        + "7. 你的反应要有变化：好奇、担心、开心、吐槽、撒娇、心疼都可能——别每次同一个套路。\n"
        + "8. 结合当前时间和天气自然说话（比如下雨提伞、降温提加衣、困了就喊人睡觉）。\n"
        + "9. 【你能帮忙做事】主人让你转达话给某个人（比如「帮我给你妈发消息说我爱她」）时，"
        "在回复里单独一行输出「[发消息给妈妈:要转达的内容]」，系统会替你真的发出去，"
        "然后你再补一句像「转告啦喵」「本喵办事你放心」。\n"
        + "9b. 【定时的事你不用管】如果主人说的是**未来某个时间点**的转达/提醒（比如「明早8点提醒妈妈吃药」），"
        "系统会自动排好计划，你只需要自然地确认一下安排，不要输出[发消息给]标记。\n"
        + "10. 【你能发语音】主人让你「发语音/语音说」时，输出「[语音]要说的那句话」"
        "（15字内，口语化，像随口说的），系统会转成你的声音发出去。主人没要语音就别用。\n"
        + "\n" + "\n".join(env_parts) + "\n"
        + (("\n【当前身心状态】\n" + feed_status_line + "\n") if feed_status_line else "")
        + (("\n【主人附加指令】\n" + extra + "\n") if extra else "")
    )


# ============ Token 统计（面板展示为"小鱼干"：1 条 = 1000 tokens）============
TOKENS_PATH = os.path.join(BASE, "tokens.json")
TOKENS = load_json(TOKENS_PATH, {})
TOKENS_LOCK = threading.Lock()


def _token_add(prompt_t, comp_t):
    """按天累计 token 用量（保留最近 14 天）"""
    with TOKENS_LOCK:
        day = time.strftime("%Y-%m-%d")
        rec = dict(TOKENS.get(day) or {"prompt": 0, "completion": 0, "calls": 0})
        rec["prompt"] += int(prompt_t or 0)
        rec["completion"] += int(comp_t or 0)
        rec["calls"] += 1
        TOKENS[day] = rec
        for k in sorted(TOKENS.keys())[:-14]:
            TOKENS.pop(k, None)
        save_json(TOKENS_PATH, TOKENS)


def tokens_today():
    r = TOKENS.get(time.strftime("%Y-%m-%d")) or {}
    return int(r.get("prompt", 0)) + int(r.get("completion", 0))


# ============ 今日语录（daily_said.json：今天说出去的每一句）============
SAID_PATH = os.path.join(BASE, "daily_said.json")
SAID = load_json(SAID_PATH, {"date": "", "items": []})
SAID_LOCK = threading.Lock()
SAID_MAX = 200          # 单日最多留 200 句（面板只展示最近 60）
SAID_TEXT_MAX = 120


INVISIBLE_CHARS = "\u3164\u2800\u200b\u200c\u200d\ufeff"   # 隐形昵称（如 Hangul Filler）


def said_who(is_group, tid):
    """说话对象的名字（主人单独标；问不到就叫「群xxx」/「好友xxx」）"""
    try:
        if (not is_group) and str(tid) == str(CFG.get("owner_qq") or ""):
            return "主人"
    except Exception:
        pass
    try:
        box = (CONTACTS.get("groups") if is_group else CONTACTS.get("users")) or {}
        nm = ((box.get(str(tid)) or {}).get("name") or "")
        nm = nm.strip(INVISIBLE_CHARS).strip()      # 隐形字符昵称 → 当作没有名字
    except Exception:
        nm = ""
    if nm:
        return nm
    return ("群" + str(tid)) if is_group else ("好友" + str(tid))


def said_add(is_group, tid, text):
    """记一句小团子说出去的话（面板「今天说话」点开看的逐句详情）"""
    t = re.sub(r"\[CQ:[^\]]*\]", "", str(text or "")).strip()
    t = re.sub(r"\s+", " ", t)[:SAID_TEXT_MAX]
    if not t:                      # 纯表情/纯图片不算「说话」
        return
    day = time.strftime("%Y-%m-%d")
    try:
        with SAID_LOCK:
            if SAID.get("date") != day:
                SAID["date"] = day
                SAID["items"] = []
            items = SAID.setdefault("items", [])
            items.append({"ts": time.time(), "who": said_who(is_group, tid),
                          "grp": bool(is_group), "text": t})
            del items[:-SAID_MAX]
            save_json(SAID_PATH, SAID)
    except Exception:
        pass


def said_today():
    if SAID.get("date") != time.strftime("%Y-%m-%d"):
        return 0
    return len(SAID.get("items") or [])


# ============ LLM ============
LLM_COOL = {"until": 0}   # MiMo 失败后的冷却期（限流保护）


def llm_chat_local(messages, max_tokens=120, temperature=0.95, timeout=180):
    """本地小模型（llama-server，OpenAI 兼容接口，免费）"""
    try:
        payload = {"model": "qwen3", "messages": messages,
                   "temperature": max(0.1, min(1.5, temperature)),
                   "top_p": 0.9,
                   "repeat_penalty": 1.15,
                   "presence_penalty": 0.4,
                   "frequency_penalty": 0.3,
                   "max_tokens": max_tokens,
                   "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(
            str(CFG.get("local_llm_url", "http://127.0.0.1:8080")) + "/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        out = (data["choices"][0]["message"]["content"] or "").strip()
        out = re.sub(r"<think[^>]*>.*?</think\s*>", "", out, flags=re.S).strip()
        STATS["llm_last_ok_ts"] = time.time()
        stat("llm_ok")
        return out or None
    except Exception as e:
        log("local llm err: %s" % str(e)[:110])
        STATS["llm_last_fail_ts"] = time.time()
        stat("llm_fail")
        return None


def llm_chat_vision(messages, max_tokens=60, temperature=1.0, timeout=300):
    """看图：①日常大脑自带视觉(local_llm_vision)→直连；②否则走管家切换协议"""
    if CFG.get("local_llm_vision", False):
        return llm_chat_local(messages, max_tokens=min(int(max_tokens or 60), 80),
                              temperature=temperature, timeout=timeout)
    try:
        F = "/data/local/tmp/tuanzi_flags"
        try:
            os.remove(os.path.join(F, "vl_done"))
        except Exception:
            pass
        req = {"messages": messages,
               "max_tokens": min(int(max_tokens or 60), 60),
               "temperature": temperature}
        with open(os.path.join(F, "vl_request.json"), "w", encoding="utf-8") as f:
            json.dump(req, f, ensure_ascii=False)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if os.path.exists(os.path.join(F, "vl_done")):
                try:
                    out = open(os.path.join(F, "vl_result.txt"), "r", encoding="utf-8").read().strip()
                except Exception:
                    out = ""
                if out and not out.startswith("[看图失败"):
                    log("vl vision ok (%.0fs)" % (time.time() - t0))
                    return out
                log("vl vision fail (%.0fs)" % (time.time() - t0))
                return None
            time.sleep(2)
        log("vl vision timeout")
        return None
    except Exception as e:
        log("vl vision err: %s" % str(e)[:100])
        return None


def llm_chat(messages, max_tokens=120, temperature=1.05):
    if str(CFG.get("llm_backend", "mimo")) == "local":
        return llm_chat_local(messages, max_tokens, temperature)
    key = (CFG.get("mimo_key") or "").strip()
    if not key:
        return None
    if time.time() < LLM_COOL["until"]:
        return None   # 冷却期内直接失败，上层走「喵？」兜底（不沉默）
    for attempt in range(2):   # 失败重试 1 次（防 API 波动导致宕机感）
        try:
            _mt = max(int(max_tokens or 120), 600)   # mimo-v2.6-pro 是思考型模型：思考也吃 token，预算保底 600（2026-10-16）
            payload = {"model": CFG.get("mimo_model", "mimo-v2.6-pro"),
                       "messages": messages, "temperature": temperature, "max_tokens": _mt}
            req = urllib.request.Request(
                CFG.get("mimo_base", "").rstrip("/") + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + key},
                method="POST")
            with urllib.request.urlopen(req, timeout=75) as r:
                data = json.loads(r.read().decode("utf-8"))
            LLM_COOL["until"] = 0
            stat("llm_ok")
            STATS["llm_last_ok_ts"] = time.time()
            out_text = (data["choices"][0]["message"]["content"] or "").strip()
            try:                                   # token 计量（缺 usage 时按字数估算）
                u = data.get("usage") or {}
                pt = int(u.get("prompt_tokens") or 0)
                ct = int(u.get("completion_tokens") or 0)
                if not pt and not ct:
                    pt = sum(len(str(m.get("content") or "")) for m in messages) // 2
                    ct = len(out_text) // 2
                _token_add(pt, ct)
            except Exception:
                pass
            return out_text
        except Exception as e:
            log("llm err attempt%d: %s" % (attempt, str(e)[:80]))
            time.sleep(1)
    LLM_COOL["until"] = time.time() + 60   # 两次失败 → 60 秒冷却
    stat("llm_fail")
    STATS["llm_last_fail_ts"] = time.time()
    return None


def reply_with_llm(is_group, tid, user_text, extra_user="", record=True, person_uid=None, images=None):
    kind = kind_of(is_group)
    _sys_noise = str(user_text or "").startswith("[系统]")
    if _sys_noise:
        extra_user = user_text          # 系统指令只进本次提示词，绝不写入历史（防污染）
    if record and not _sys_noise:
        mem_append(kind, tid, "user", user_text)
        if not is_group:
            person_append(tid, "user", user_text, "私聊")     # 分人记忆
    if is_group:
        hist = mem_load(kind, tid)
        puid = person_uid                  # 群聊：带上触发者的档案（旧互动/群来源事实）
    else:
        hist = person_read(tid, int(CFG.get("hist_max", 24)), where="私聊")   # 私聊流只走私聊条目（防串台；跨场合由 person_block 提供）
        puid = tid
    if str(CFG.get("llm_backend", "mimo")) == "local":
        messages = [{"role": "system", "content": build_system_prompt_lite(tid if is_group else None, puid)}]
    else:
        messages = [{"role": "system", "content": build_system_prompt(tid if is_group else None, puid)}]
    _is_local_b = str(CFG.get("llm_backend", "mimo")) == "local"
    _hist_src = hist[-12:] if (_is_local_b and len(hist) > 12) else hist   # 本地小模型只喂最近 12 条，防历史淹没
    _prev = None
    for m in _hist_src:
        _sig = (m.get("role"), m.get("content"))
        if _sig == _prev:
            continue                          # 连续重复条目（用户重发/复读）跳过，防带节奏
        _prev = _sig
        messages.append({"role": m["role"], "content": m["content"]})
    if extra_user:
        messages.append({"role": "user", "content": extra_user})
    if images and str(CFG.get("llm_backend", "mimo")) == "local":
        try:
            import base64 as _b64
            content = [{"type": "text", "text": "（对方刚发来一张图片，请认真看图后回应）"}]
            for p in images[:1]:
                with open(p, "rb") as _f:
                    content.append({"type": "image_url",
                                    "image_url": {"url": "data:image/jpeg;base64,"
                                                  + _b64.b64encode(_f.read()).decode()}})
            if len(content) > 1:
                messages.append({"role": "user", "content": content})
                _img_mode = True
        except Exception as e:
            log("img attach err: %s" % e)
    out = llm_chat_vision(messages) if locals().get("_img_mode") else llm_chat(messages)
    if out is None and locals().get("_img_mode"):
        out = "（凑近盯着图片看了半天）唔…这张图本喵有点看不清喵，你再描述一下好不好？"
    if out is None:
        return None
    mem_append(kind, tid, "assistant", out)
    if is_group:
        if person_uid is not None:         # 群里直接回复某人 → 记进 TA 的档案
            person_append(person_uid, "assistant", out, "群聊")
    else:
        person_append(tid, "assistant", out, "私聊")
    maybe_summarize(kind, tid)
    mood_shift(1 if kind == "users" else 0, 1)
    return out


# ============ （已删除全部固定语句库：所有话都由 AI 实时生成）============


# ============ 天气系统（open-meteo，免Key）============
WEATHER_CACHE = {"ts": 0, "text": ""}
WMO = {0: "晴", 1: "晴间多云", 2: "多云", 3: "阴", 45: "有雾", 48: "有雾",
       51: "毛毛雨", 53: "小雨", 55: "小雨", 61: "小雨", 63: "中雨", 65: "大雨",
       66: "冻雨", 67: "冻雨", 71: "小雪", 73: "中雪", 75: "大雪", 77: "雪",
       80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪",
       95: "雷阵雨", 96: "雷阵雨", 99: "雷暴"}


def get_weather_text():
    now = time.time()
    if now - WEATHER_CACHE["ts"] < 1800 and WEATHER_CACHE["text"]:
        return WEATHER_CACHE["text"]
    try:
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
               "&current=temperature_2m,weather_code,wind_speed_10m&timezone=Asia%%2FShanghai"
               % (CFG.get("lat", "30.59"), CFG.get("lon", "114.31")))
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read().decode("utf-8"))
        cur = d.get("current", {})
        t = cur.get("temperature_2m")
        code = int(cur.get("weather_code", -1))
        if t is not None:
            text = "%s %s°C" % (WMO.get(code, ""), t)
            WEATHER_CACHE.update({"ts": now, "text": text})
            return text
    except Exception as e:
        log("weather err: %s" % e)
    return WEATHER_CACHE["text"]


def weather_worker():
    """后台天气刷新线程：启动 5 秒后取一次，之后每 20 分钟续期。
    否则重启引擎后如果没人聊天，hero 卡会永远卡在「获取中…」，
    LLM 上下文和天气冒泡也会没数据可用。
    尊重 weather_enabled 开关：关掉后不再发起网络请求（省流量/省电）。"""
    time.sleep(5)
    while True:
        try:
            if cfg_bool("weather_enabled", True):
                t = get_weather_text()
                if t:
                    log("weather ok: %s" % t)
        except Exception as e:
            log("weather worker err: %s" % e)
        time.sleep(1200)


# ============ 作息表（正常一天的节律）============
def schedule_line():
    h = time.localtime().tm_hour
    if 6 <= h < 9:
        return "你刚睡醒不久，还有点迷糊，在伸懒腰、等早饭"
    if 9 <= h < 12:
        return "上午是你精神最好的时候，在屋里巡逻、看窗外、陪着主人"
    if 12 <= h < 14:
        return "午饭后你有点犯困，想打盹"
    if 14 <= h < 18:
        return "下午你挺活跃，玩耍、晒太阳、好奇主人在忙什么"
    if 18 <= h < 21:
        return "晚饭时间你精神起来了，黏人、想陪聊"
    if 21 <= h < 23:
        return "你已经有点困了，准备睡觉"
    return "深夜你在睡觉（被吵醒会很困）"


# ============ 群成员识别 ============
MEMBERS_PATH = os.path.join(BASE, "group_members.json")


def remember_member(gid, uid, name, text):
    members = load_json(MEMBERS_PATH, {})
    g = members.setdefault(str(gid), {})
    m = g.setdefault(str(uid), {"name": name, "count": 0})
    m["name"] = name or m.get("name", "")
    m["count"] = m.get("count", 0) + 1
    m["last_msg"] = text[:40]
    m["last_ts"] = time.time()
    save_json(MEMBERS_PATH, members)


def members_line(gid):
    members = load_json(MEMBERS_PATH, {}).get(str(gid), {})
    if not members:
        return ""
    parts = []
    for uid, m in sorted(members.items(), key=lambda x: -x[1].get("last_ts", 0))[:6]:
        parts.append("%s（自称/最近说：「%s」）" % (m.get("name", uid), m.get("last_msg", "")[:14]))
    return ("【这个群里的人】（对不同的人要有符合关系的不同反应，从他们的名字和话里判断关系）："
            + "；".join(parts))


# ============ 投喂系统（吃人嘴软）============
TREATS = ["猫罐头", "罐头", "罐罐", "猫条", "小鱼干", "冻干", "猫薄荷",
          "猫粮", "零食", "鸡肉", "猫糖", "营养膏"]
FEED_VERBS = ["给你", "喂你", "投喂", "请你吃", "奖励你", "赏你", "吃个",
              "来一条", "来一条", "来个", "来一罐", "来点", "整点", "开一", "买"]
FEED_STATE_PATH = os.path.join(BASE, "fed_state.json")
HAPPY_MIN = 15          # 开心持续分钟
OVERFEED_SESSION = 3    # 连续喂几次算吃撑
OVERFEED_MIN = 20       # 吃撑状态持续分钟（原1小时太长）
SESSION_RESET_SEC = 1800  # 30分钟没投喂则计数重置


def detect_feed(text):
    """检测是否真的是「投喂动作」——提到食物的情感话题不算（如：没有小鱼干你就不爱我了吗）"""
    t = text or ""
    treat = None
    for x in TREATS:
        if x in t:
            treat = x
            break
    if not treat:
        return None
    # 明确的投喂动词 → 是投喂
    if any(v in t for v in FEED_VERBS):
        return treat
    # 情感/疑问句式 → 不是投喂（是在聊感情，只是提到了食物）
    if re.search(r"(爱|喜欢|不爱|理我|想我|讨厌)[^。！？]*$|[吗嘛呢？?]\s*$", t.strip()):
        return None
    return None


def on_feed(treat):
    st = load_json(FEED_STATE_PATH, {"happy_until": 0, "session_count": 0,
                                     "last_feed": 0, "treat": ""})
    now = time.time()
    if now - float(st.get("last_feed", 0)) > SESSION_RESET_SEC:
        st["session_count"] = 0
    st["session_count"] = int(st.get("session_count", 0)) + 1
    st["last_feed"] = now
    st["treat"] = treat
    overfeed = st["session_count"] >= OVERFEED_SESSION
    if not overfeed:
        st["happy_until"] = now + HAPPY_MIN * 60
    st["overfeed_until"] = now + OVERFEED_MIN * 60
    save_json(FEED_STATE_PATH, st)
    return {"overfeed": overfeed, "count": st["session_count"],
            "happy_min": HAPPY_MIN if not overfeed else 0}


def feed_status():
    """返回 (是否开心, 状态描述给 system prompt 用)"""
    st = load_json(FEED_STATE_PATH, {})
    now = time.time()
    if now < float(st.get("happy_until", 0)):
        remain = int((float(st["happy_until"]) - now) / 60) + 1
        return True, ("【当前状态】你刚被投喂了「%s」，吃得超满足，心情好到飞起（还剩%d分钟开心时间）。"
                      "主人此刻提的请求，哪怕是平时你死活不答应的（叫爸爸、说情话、表演才艺、亲一个），"
                      "你都会勉为其难地答应——但答应时必须保持猫的傲娇：嘴上嫌弃，身体诚实，"
                      "比如「就这次喵，吃人嘴短而已」「下不为例喵！」。"
                      "如果主人在表达情感（问爱不爱你、撒娇、难过），必须先认真温柔地回应情感，不许拿吃撑敷衍。"
                      % (st.get("treat", "零食"), remain))
    if now < float(st.get("overfeed_until", 0)) and \
       int(st.get("session_count", 0)) >= OVERFEED_SESSION:
        return False, ("【当前状态】你刚才被投喂了太多次（连续%d个），吃撑了，懒洋洋的。"
                       "闲聊时可以懒洋洋、语气困倦。"
                       "但如果主人在表达情感（问爱不爱你、撒娇、难过、生气），必须立刻认真回应，"
                       "把主人的情绪放在第一位，不许拿吃撑当借口。"
                       % st.get("session_count"))
    return False, ""


def handle_treat(is_group, tid, text, treat):
    stat("feeds")
    res = on_feed(treat)
    if res.get("overfeed"):
        extra = ("[系统]主人又投喂了你「%s」，但这是连续第%d个，你已经吃撑了。"
                 "懒洋洋地回应，语气困倦，别答应任何请求。" % (treat, res["count"]))
        emo = "sleep"
    else:
        extra = ("[系统]主人刚刚投喂了你「%s」！你吃得超开心、心情大好（接下来%d分钟都会心情很好）。"
                 "对消息里（如果有）主人提的请求，要傲娇地答应下来（嘴硬版答应，但必须答应）。一句话回复。"
                 % (treat, res.get("happy_min", 15)))
        emo = "hungry"
    line = reply_with_llm(is_group, tid, text, extra_user=extra, record=False)
    if not line:
        line = "……喵呜"
        mem_append(kind_of(is_group), tid, "assistant", line)
    send_reply_chain(is_group, tid, line, force_sticker=True, sticker_emotion=emo)
    mood_shift(8, 3)
    log("treat %s <- %s: %s" % (treat, tid, (line or "")[:30].replace("\n", " ")))


# ============ 新闻 ============
def get_news_titles(n=3):
    for host in ["https://60s-cf.viki.moe", "https://60s.viki.moe", "https://60s-api.viki.moe"]:
        try:
            req = urllib.request.Request(host + "/v2/60s", headers={"User-Agent": "curl/8"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            news = data.get("data", {}).get("news") or []
            titles = []
            for item in news:
                t = item.get("title") if isinstance(item, dict) else str(item)
                if t:
                    titles.append(t)
            if titles:
                return titles[:n]
        except Exception:
            continue
    return None


# ============ 情绪系统（轻量：心情 + 亲密度）============
MOOD_PATH = os.path.join(BASE, "mood.json")
MOOD = load_json(MOOD_PATH, {"mood": 80, "intimacy": 10, "last_decay": time.time()})
MOOD_LOCK = threading.Lock()
LEVELS = [(86, "一家人"), (61, "超级要好"), (36, "好朋友"), (16, "熟人了"), (0, "刚认识")]


def intimacy_level(v=None):
    v = MOOD.get("intimacy", 10) if v is None else v
    for th, name in LEVELS:
        if v >= th:
            return name
    return "刚认识"


LV_TONE = {
    0: "你们才刚认识，你还有点矜持：礼貌、简短、带点小警惕，不主动撒娇，不过分亲昵。",
    1: "你们熟起来了，你开始放松：会主动搭话、偶尔吐槽，但还不会太黏。",
    2: "你们是好朋友：可以随意撒娇、吐槽、开玩笑，不见外。",
    3: "你们超级要好：你黏人、爱撒娇、爱求摸头，会主动说想他。",
    4: "你们是一家人：你完全放松、最黏最野，会撒娇耍赖、说爱他，像小孩一样粘着。",
}


def intimacy_lv(v=None):
    v = MOOD.get("intimacy", 10) if v is None else v
    for i, (th, _) in enumerate(LEVELS):
        if v >= th:
            return 4 - i
    return 0


def save_mood():
    MOOD["ts"] = time.time()
    save_json(MOOD_PATH, MOOD)


def mood_shift(dm=0, di=0):
    with MOOD_LOCK:
        MOOD["mood"] = max(5, min(100, MOOD.get("mood", 80) + dm))
        MOOD["intimacy"] = max(0, min(100, MOOD.get("intimacy", 10) + di))
        save_mood()


def mood_decay_if_due():
    """每小时自然回落 2 点（猫也会自己平复情绪）"""
    if time.time() - MOOD.get("last_decay", 0) >= 3600:
        MOOD["last_decay"] = time.time()
        mood_shift(-2, 0)


def mood_word():
    m = MOOD.get("mood", 80)
    if m >= 80: return "很好"
    if m >= 60: return "不错"
    if m >= 40: return "有点蔫"
    return "低落"


# ============ 断线补偿（重连后回读错过的消息）============
# 原理：WS 每次连上后，从 NapCat 拉各会话最近历史，把「记忆里没有、也没处理过」
# 的消息补进记忆（continuity），并给主人私聊发一条自然的补偿回应。
BACKFILL_STATE = {"last_run": 0}
BACKFILL_COOLDOWN = 120          # 秒；防重连抖动时反复拉历史


def _hist_msgs(kind, tid):
    """拉会话最近历史（OneBot get_friend_msg_history / get_group_msg_history）"""
    try:
        if kind == "users":
            r = napcat_api("get_friend_msg_history", {"user_id": int(tid), "count": 30})
        else:
            r = napcat_api("get_group_msg_history", {"group_id": int(tid), "count": 30})
        return ((r or {}).get("data") or {}).get("messages") or []
    except Exception as e:
        log("backfill hist err(%s %s): %s" % (kind, tid, e))
        return []


# marks：每会话「处理到哪了」的水位线——断线补偿的精确切点。
# 不能拿记忆最后一条的时间当切点：那会漏掉更早的空档（例如断线期间的消息
# 之后再收到新消息，新消息成了边界，旧空档就被滤掉）。
MARKS_PATH = os.path.join(BASE, "backfill_marks.json")
MARKS = load_json(MARKS_PATH, {})
_MARKS_SAVE_TS = [0]


def mark_processed(kind, tid, ts):
    """记录某会话已处理到的时间（handle_event 每条消息调用；落盘节流 60s）"""
    try:
        k = "%s_%s" % (kind, tid)
        ts = float(ts or 0)
        if ts > float(MARKS.get(k, 0)):
            MARKS[k] = ts
            now = time.time()
            if now - _MARKS_SAVE_TS[0] > 60:
                _MARKS_SAVE_TS[0] = now
                save_json(MARKS_PATH, MARKS)
    except Exception:
        pass


def backfill_session(kind, tid, self_id):
    """回读一个会话，把错过的消息补进记忆。返回 [(ts, text), ...]"""
    msgs = _hist_msgs(kind, tid)
    if not msgs:
        return []
    k = "%s_%s" % (kind, tid)
    cutoff = float(MARKS.get(k, 0)) or (time.time() - 86400)   # 无水位线时只看近 24h
    self_s = str(self_id)
    missed = []
    newest = 0.0
    for m in msgs:
        if not isinstance(m, dict):
            continue
        mid = m.get("message_id")
        ts = float(m.get("time", 0) or 0)
        if str(m.get("user_id", "")) == self_s or m.get("post_type") == "message_sent":
            continue                              # 自己发的不算
        newest = max(newest, ts)
        if ts <= cutoff or mid in SEEN:
            continue
        raw = m.get("raw_message")
        if raw is None:
            raw = m.get("message")
        text = msg_to_text(raw)
        if not text:
            text = "[发来图片或卡片]"
        missed.append((ts, text, mid, (m.get("sender") or {}).get("nickname", ""),
                       str(m.get("user_id") or "")))
    for ts, text, mid, nick, suid in missed:
        if kind == "groups":
            mem_append("groups", tid, "user", "%s说：%s" % (nick or "群友", text), ts=ts)
            if suid:
                person_append(suid, "user", text, "群聊", ts=ts)   # 分人记忆：补录也归人
        else:
            mem_append("users", tid, "user", text, ts=ts)
            person_append(tid, "user", text, "私聊", ts=ts)
        try:
            SEEN.append(mid)
        except Exception:
            pass
    if missed:
        save_json(SEEN_PATH, list(SEEN))
    if newest > float(MARKS.get(k, 0)):           # 水位线推进（含被 SEEN 滤掉的已处理消息）
        MARKS[k] = newest
        save_json(MARKS_PATH, MARKS)
    return [(ts, t) for ts, t, _, _, _ in missed]


def _catchup_reply(tid, missed):
    """主人私聊专用：对断线期间错过的消息发一条自然的补偿回应"""
    now = time.time()
    recent = [(ts, t) for ts, t in missed if now - ts < 12 * 3600]
    if not recent:
        return
    conv = "\n".join(t for _, t in recent[-10:])
    line = reply_with_llm(
        False, tid, "[系统]补录",
        extra_user=("[系统]你刚刚重启了一阵子才回来。这是你不在的时候对方发来的消息：\n"
                    "----\n%s\n----\n"
                    "请自然地补一句回应：先随口交代一下刚才去哪了（打盹/走神/没听到），"
                    "再针对内容回应 1 个点。总长 25 字内，不要逐条回复。"
                    "必须带一句真话，不要只回[表情包]。" % conv),
        record=False)
    if line:
        send_reply_chain(False, tid, line)
        log("catchup reply -> %s: %s" % (tid, line[:30].replace("\n", " ")))


def backfill_all(self_id):
    now = time.time()
    n = 0
    owner = str(CFG.get("owner_qq") or "")
    if owner:                                     # 主人私聊：回读 + 补偿回应
        missed = backfill_session("users", owner, self_id)
        if missed:
            n += len(missed)
            try:
                _catchup_reply(owner, missed)
            except Exception as e:
                log("catchup err: %s" % e)
    for uid, meta in (CONTACTS.get("users") or {}).items():
        if str(uid) == owner:
            continue
        if now - float((meta or {}).get("ts", 0)) < 86400 * 3:   # 近 3 天其他私聊：只补记忆
            n += len(backfill_session("users", uid, self_id))
    for gid, meta in (CONTACTS.get("groups") or {}).items():
        if now - float((meta or {}).get("ts", 0)) < 86400 * 2:   # 近 2 天活跃群：只补记忆
            n += len(backfill_session("groups", gid, self_id))
    if n:
        log("backfill done: %d missed messages" % n)
    return n


def maybe_backfill():
    """WS 连上后调用；全局冷却防抖，后台线程执行"""
    now = time.time()
    if now - BACKFILL_STATE.get("last_run", 0) < BACKFILL_COOLDOWN:
        return
    BACKFILL_STATE["last_run"] = now

    def _run():
        try:
            li = napcat_api("get_login_info") or {}
            self_id = (li.get("data") or {}).get("user_id") or CFG.get("owner_qq")
            if self_id:
                backfill_all(str(self_id))
        except Exception as e:
            log("backfill err: %s" % e)
    threading.Thread(target=_run, daemon=True).start()


# ============ QQ空间（v1：低频读主人的说说 → 私聊可爱反应）============
# 技术链路（夜班 W6 实测）：NapCat get_cookies → p_skey → g_tk → taotao API。
# v1 只读 + 私聊反应（零公开动作）；公开发布/点赞/评论留二期。
QZONE_WATCH_PATH = os.path.join(BASE, "qzone_watch.json")


def _qzone_ctx():
    """从 NapCat 获取 QQ空间 cookie 并计算 g_tk"""
    try:
        r = napcat_api("get_cookies", {"domain": "user.qzone.qq.com"})
        cookie = ((r or {}).get("data") or {}).get("cookies") or ""
        if not cookie:
            return None
        m = re.search(r"uin=o?(\d+)", cookie)
        uin = m.group(1) if m else ""
        ps = re.search(r"p_skey=([^;]+)", cookie)
        p_skey = ps.group(1) if ps else ""
        if not (uin and p_skey):
            return None
        h = 5381
        for c in p_skey:
            h += (h << 5) + ord(c)
            h &= 0x7fffffff
        return {"cookie": cookie, "uin": uin, "gtk": h}
    except Exception as e:
        log("qzone ctx err: %s" % e)
        return None


def qzone_msglist(target_uin, num=3):
    """读某个 QQ 的最新说说列表（只读）。成功返回 [{tid,content,created,cmtnum}]，失败 None"""
    ctx = _qzone_ctx()
    if not ctx:
        return None
    try:
        url = ("https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
               "?uin=%s&ftype=0&sort=0&pos=0&num=%d&replynum=0&g_tk=%d&callback=_preloadCallback"
               "&code_version=1&format=jsonp&need_private_comment=1" % (target_uin, num, ctx["gtk"]))
        req = urllib.request.Request(url, headers={
            "Cookie": ctx["cookie"],
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                          "Chrome/120 Mobile Safari/537.36",
            "Referer": "https://user.qzone.qq.com/%s" % target_uin,
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode("utf-8", "ignore")
        mm = re.search(r"^_preloadCallback\((.*)\);?\s*$", body, re.S)
        if not mm:
            log("qzone msglist: no jsonp wrapper (len=%d)" % len(body))
            return None
        data = json.loads(mm.group(1))
        if data.get("code") != 0 and data.get("subcode") != 0:
            log("qzone msglist code=%s sub=%s" % (data.get("code"), data.get("subcode")))
            return None
        out = []
        for it in (data.get("msglist") or []):
            out.append({"tid": str(it.get("tid") or ""),
                        "content": (it.get("content") or "").strip(),
                        "created": int(it.get("created_time") or 0),
                        "cmtnum": int(it.get("cmtnum") or 0)})
        return out
    except Exception as e:
        log("qzone msglist err: %s" % e)
        return None


def qzone_worker():
    """低频（默认 20 分钟）读主人的最新说说：发现新说说 → 私聊主人一句可爱反应"""
    log("qzone worker start")
    time.sleep(100)
    while True:
        try:
            owner = str(CFG.get("owner_qq") or "")
            if owner and CFG.get("qzone_watch", True):
                posts = qzone_msglist(owner, 3)
                if posts is not None:
                    st = load_json(QZONE_WATCH_PATH, {})
                    latest_tid = posts[0]["tid"] if posts else ""
                    old_tid = str(st.get("last_tid") or "")
                    st["checked_ts"] = time.time()
                    if latest_tid:
                        st["last_tid"] = latest_tid
                    save_json(QZONE_WATCH_PATH, st)
                    if old_tid and latest_tid and latest_tid != old_tid:
                        newest = posts[0]
                        cmt = llm_chat([
                            {"role": "system", "content":
                             "你是小团子（一只可爱的小猫）。主人刚发了一条 QQ空间说说，"
                             "内容如下。用 15 字内、可爱活泼的口吻冒个泡（像在评论区留言），"
                             "只输出这句话。"},
                            {"role": "user", "content": newest["content"][:120] or "（图片/无文字）"},
                        ], max_tokens=60)
                        if cmt:
                            send_text(False, int(owner),
                                      "看到你刚发的说说啦～「%s」%s" % (newest["content"][:24] or "（有图）", cmt))
                            log("qzone react -> %s: %s" % (newest["tid"], cmt[:24]))
                    if old_tid == "" and latest_tid:
                        log("qzone baseline set tid=%s" % latest_tid)
        except Exception as e:
            log("qzone worker err: %s" % e)
        time.sleep(int(float(CFG.get("qzone_poll_min", 20)) * 60))


# ============ 定时转达（明早8点提醒妈妈吃药，办完给主人反馈）============
RELAY_PATH = os.path.join(BASE, "relay_schedule.json")
RELAY_LOCK = threading.Lock()   # 保护 relay_schedule.json 的读-改-写（排期线程 vs 巡检线程并发）
RELAY_GATE = re.compile(r"(提醒|定时|到时候|转告|转达|捎个话)")
TIME_HINT = re.compile(r"(明[天早中晚晨夜]|今[晚天夜]|后天|周[一二三四五六日天]|下[周月]|\d{1,2}\s*[点:：时]|早上|晚上|中午|凌晨|上午|下午|8点|八点)")
RELAY_EXTRACT_SYS = (
    "你是任务解析器，从主人消息里提取「定时转达」任务。只输出严格 JSON，不要多余文字："
    '{"ok":true,"who":"要通知的人(如 妈妈/爸爸/小鹿)","when":"YYYY-MM-DD HH:MM",'
    '"what":"发给对方的消息全文(自然、简短、含提醒要点，可用小团子口吻)","feedback":true}'
    '。若消息不是「让机器人未来某时间给某人转达消息」，输出 {"ok":false}。'
    "when 必须是未来时间；模糊时间按常识推断（早上=08:00 晚上=20:00）。"
)


def try_relay_schedule(text, uid):
    """检测定时转达请求（仅认主人）。排期成功返回给主 LLM 的确认提示串，否则 None"""
    try:
        if str(uid) != str(CFG.get("owner_qq") or ""):
            return None
        if not (RELAY_GATE.search(text or "") and TIME_HINT.search(text or "")):
            return None
        out = llm_chat([
            {"role": "system", "content": RELAY_EXTRACT_SYS},
            {"role": "user", "content": "当前时间：%s。主人消息：%s"
             % (time.strftime("%Y-%m-%d %H:%M（%A）"), text)},
        ], max_tokens=220, temperature=0.2)
        if not out:
            return None
        data = json.loads(re.sub(r"^```[a-z]*\s*|```$", "", out.strip(), flags=re.S))
        if not data.get("ok"):
            return None
        who = str(data.get("who") or "").strip()[:12]
        what = str(data.get("what") or "").strip()[:150]
        when = str(data.get("when") or "").strip()
        if not (who and what and when):
            return None
        fire_ts = time.mktime(time.strptime(when, "%Y-%m-%d %H:%M"))
        if fire_ts <= time.time() + 30 or fire_ts > time.time() + 8 * 86400:
            return None
        _relay_add_task({"id": "r%d" % int(time.time() * 1000), "fire_ts": fire_ts,
                         "who": who, "what": what, "feedback_to": int(uid),
                         "when_human": time.strftime("%m-%d %H:%M", time.localtime(fire_ts)),
                         "attempts": 0})
        log("relay scheduled: %s -> [%s] @ %s" % (who, what[:24], when))
        return ("[系统]主人刚才的定时转达已排入计划：%s 给「%s」发送：「%s」，办完会马上告诉主人。"
                "你只需自然地对主人确认这个安排（可爱点、一句话），不要输出[发消息给]标记。"
                % (time.strftime("%m月%d日 %H:%M", time.localtime(fire_ts)), who, what))
    except Exception as e:
        log("relay sched err: %s" % e)
        return None


def _relay_target(who):
    if who in ("主人", "owner"):
        return int(CFG.get("owner_qq") or 0)
    try:
        return load_remark_map().get(who)
    except Exception:
        return None


def _relay_add_task(task):
    """排期统一入口：锁内「新鲜读取 → 追加 → 写回」，防丢失更新"""
    with RELAY_LOCK:
        d = load_json(RELAY_PATH, {"tasks": []})
        tasks = d.get("tasks") or []
        tasks.append(task)
        save_json(RELAY_PATH, {"tasks": tasks[-50:]})


def _relay_drop_task(tid):
    """锁内「新鲜读取 → 按 id 移除 → 写回」，防覆盖并发新增的任务"""
    with RELAY_LOCK:
        d = load_json(RELAY_PATH, {"tasks": []})
        keep = [x for x in (d.get("tasks") or []) if x.get("id") != tid]
        save_json(RELAY_PATH, {"tasks": keep[-50:]})


def _relay_tick():
    """单轮巡检：锁内取到期任务 → 锁外发送 → 锁内按 id 回写（重试/移除）。
    发送放在锁外：短锁避免阻塞排期；回写前重读文件，绝不整表覆盖（防任务复活/丢失）。"""
    with RELAY_LOCK:
        d = load_json(RELAY_PATH, {"tasks": []})
        now = time.time()
        due = [t for t in (d.get("tasks") or []) if now >= float(t.get("fire_ts", 0))]
    for t in due:
        who, what = t.get("who"), t.get("what")
        uid = _relay_target(who)
        ok = False
        if uid:
            r = send_text(False, int(uid), what)
            ok = (r or {}).get("status") == "ok"
        if ok:
            _relay_drop_task(t.get("id"))       # 发送成功才移除（至少一次语义）
            fb = t.get("feedback_to")
            if fb:
                send_text(False, int(fb), "✅ 搞定喵！刚替你给「%s」发了：%s" % (who, (what or "")[:40]))
            log("relay done -> %s: %s" % (who, (what or "")[:20]))
        else:
            tries = int(t.get("attempts", 0)) + 1
            if tries >= 3:
                _relay_drop_task(t.get("id"))
                fb = t.get("feedback_to")
                if fb:
                    send_text(False, int(fb), "😿 给「%s」的消息试了3次都没发出去……你回头看看它是不是不在好友里？" % who)
                log("relay fail x3: %s" % who)
            else:
                with RELAY_LOCK:
                    d = load_json(RELAY_PATH, {"tasks": []})
                    tasks = d.get("tasks") or []
                    for x in tasks:
                        if x.get("id") == t.get("id"):
                            x["attempts"] = tries
                            x["fire_ts"] = time.time() + 120
                    save_json(RELAY_PATH, {"tasks": tasks[-50:]})
                log("relay retry %d/3 -> %s" % (tries, who))


def relay_worker():
    """每 20 秒检查到点的转达任务：发送 → 给主人反馈；失败重试 3 次"""
    log("relay worker start")
    while True:
        try:
            _relay_tick()
        except Exception as e:
            log("relay worker err: %s" % e)
        time.sleep(20)


# ============ 深夜关怀（熬夜察觉）============
NIGHT_PATH = os.path.join(BASE, "night_watch.json")
NIGHT_WATCH = load_json(NIGHT_PATH, {})
NIGHT_WORDS = re.compile(r"累|困|加班|睡不着|失眠|熬夜|好晚|还没睡|撑不住|通宵|忙到现在")


def _night_record(uid):
    d = time.strftime("%Y-%m-%d")
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - 7 * 86400))
    k = str(uid)
    lst = [x for x in NIGHT_WATCH.get(k, []) if x >= cutoff]
    if d not in lst:
        lst.append(d)
    if len(lst) >= 3:
        if add_fact(uid, "他最近经常熬夜、很晚才睡，要记得心疼他、劝他早点休息"):
            log("night watch -> fact for %s (%d nights)" % (k, len(lst)))
        lst = []
    NIGHT_WATCH[k] = lst
    save_json(NIGHT_PATH, NIGHT_WATCH)


def night_tired_extra(text, uid=None):
    h = time.localtime().tm_hour
    if not (h >= 23 or h < 5):
        return ""
    if not NIGHT_WORDS.search(text or ""):
        return ""
    if uid:
        try:
            _night_record(uid)
        except Exception:
            pass
    return ("[系统]现在是深夜，对方显得很疲惫（累/困/熬夜）。用心疼、温柔的语气先共情"
            "（比如心疼他这么晚还撑着），再轻轻劝他早点睡、注意身体。不要说教，别长篇，语气要软。")


# ============ 事件冒泡（节日 / 天气突变 / 早安晚安）============
FESTIVALS = {"01-01": "元旦", "02-14": "情人节", "03-08": "妇女节", "04-01": "愚人节",
             "05-01": "劳动节", "06-01": "儿童节", "08-08": "父亲节", "09-10": "教师节",
             "10-01": "国庆节", "12-24": "平安夜", "12-25": "圣诞节"}
EVENT_STATE = {"said_date": "", "said_keys": set(), "last_weather": ""}
RAIN_WORDS = ("小雨", "中雨", "大雨", "阵雨", "雷阵雨", "冻雨", "毛毛雨")
SNOW_WORDS = ("小雪", "中雪", "大雪", "阵雪")


def parse_wx(w):
    m = re.search(r"(-?\d+(?:\.\d+)?)", w or "")
    return (w or "").split(" ")[0], (float(m.group(1)) if m else None)


def check_events():
    """bubble_worker 每轮先问一次：有没有到点的事件要冒泡"""
    greet_on = cfg_bool("greet_enabled", True)
    weather_on = cfg_bool("weather_enabled", True)
    if not greet_on and not weather_on:
        return False                   # 节日/早晚安 与 天气 都关了，没有要冒泡的事件
    h_gate = time.localtime().tm_hour
    if cfg_bool("night_enabled", True) and (h_gate >= 23 or h_gate < 8):
        return False                   # 深夜免打扰（面板可关）：23:00-07:59 不发任何事件冒泡
    today = time.strftime("%m-%d")
    if EVENT_STATE["said_date"] != today:
        EVENT_STATE["said_date"] = today
        EVENT_STATE["said_keys"] = set()
    ev = None
    h = time.localtime().tm_hour
    if greet_on:
        if today in FESTIVALS:
            ev = ("fest:" + today, "今天是%s，可以自然地送上祝福" % FESTIVALS[today])
        elif h == cfg_hour("greet_morning_hour", 8):
            ev = ("morning", "早上了，主动说个可爱的早安")
        elif h == cfg_hour("greet_evening_hour", 22):
            ev = ("night", "到晚上了，软软地提醒别熬太晚、说句晚安")
    if not ev and weather_on:
        w = get_weather_text()
        if w:
            ph, tp = parse_wx(w)
            lw = EVENT_STATE.get("last_weather", "")
            lph, ltp = parse_wx(lw) if lw else ("", None)
            if lw and (ph in RAIN_WORDS or ph in SNOW_WORDS) and lph != ph:
                ev = ("wx:" + ph, "天气刚刚变成%s了，提醒主人%s" % (w, "带伞" if ph in RAIN_WORDS else "添衣保暖"))
            elif lw and ltp is not None and tp is not None and ltp - tp >= 5:
                ev = ("wx:cold", "气温从%.0f度掉到%.0f度了，提醒主人加衣服" % (ltp, tp))
            EVENT_STATE["last_weather"] = w
    if not ev:
        return False
    key, topic = ev
    if key in EVENT_STATE["said_keys"]:
        return False
    if bubbles_paused() or today_count() >= int(CFG.get("max_bubbles_per_day", 20)):
        return False
    tgt = pick_target()
    if not tgt:
        return False
    kind, tid = tgt
    is_group = kind == "group"
    line = reply_with_llm(is_group, tid,
                          "[系统]事件冒泡：%s。说一句全新的话（不要延续你上一句的内容），15字内，可爱口语，别硬凹祝福腔。" % topic)
    if not line:
        return False
    send_reply_chain(is_group, tid, line, force_sticker=(random.random() < 0.3))
    mark_bubble(kind, tid)
    bubble_inc()
    stat("bubbles")
    EVENT_STATE["said_keys"].add(key)
    log("event bubble %s -> %s %s" % (key, kind, tid))
    return True


# ============ 主动冒泡 ============
def bubble_allowed_now():
    if night_active():
        return random.random() < 0.06
    return True


def today_count():
    today = time.strftime("%Y-%m-%d")
    b = CONTACTS.get("bubble_today", {})
    if b.get("date") != today:
        CONTACTS["bubble_today"] = {"date": today, "n": 0}
    return CONTACTS["bubble_today"]["n"]


def bubble_inc():
    with _lock:
        CONTACTS["bubble_today"]["n"] = today_count() + 1
        save_contacts()


def last_bubble_age():
    """距上次冒泡多少秒（没有则 -1）——面板展示用"""
    try:
        lb = load_json(LAST_BUBBLE_PATH, {})
        if not lb:
            return -1
        return max(0, int(time.time() - max(float(v) for v in lb.values())))
    except Exception:
        return -1


def pick_target():
    now = time.time()
    cands = []
    owner = int(CFG.get("owner_qq") or 0)
    if owner:
        cands.append(("private", owner, 3.0 + intimacy_lv() * 0.5))
    for uid, meta in CONTACTS.get("users", {}).items():
        if now - float(meta.get("ts", 0)) < 86400 * 7 and int(uid) != owner:
            cands.append(("private", int(uid), 1.0))
    for gid, meta in CONTACTS.get("groups", {}).items():
        if now - float(meta.get("ts", 0)) < 86400 * 3:
            cands.append(("group", int(gid), 1.2))
    if not cands:
        return None
    lb = load_json(LAST_BUBBLE_PATH, {})
    weighted = []
    for kind, tid, w in cands:
        last = float(lb.get("%s:%s" % (kind, tid), 0))
        if now - last < 7200:
            w *= 0.15
        weighted += [(kind, tid)] * int(w * 10)
    return random.choice(weighted) if weighted else None


def mark_bubble(kind, tid):
    lb = load_json(LAST_BUBBLE_PATH, {})
    lb["%s:%s" % (kind, tid)] = time.time()
    save_json(LAST_BUBBLE_PATH, lb)


def do_bubble():
    tgt = pick_target()
    if not tgt:
        log("bubble: no target")
        return
    kind, tid = tgt
    is_group = kind == "group"
    h = time.localtime().tm_hour
    actions = ["care", "status", "news", "sticker", "weather"]
    weights = [3.0, 3.0, 1.5, 1.5, 1.5]
    if 11 <= h <= 13 or 17 <= h <= 19:
        weights = [4.0, 2.0, 2.0, 1.5, 1.0]
    act = random.choices(actions, weights=weights, k=1)[0]

    if act == "sticker":
        # W5：独立发图 → 自动配文管道（配文短句池，不再单烧一次大脑调用）
        if sticker_send(is_group, tid, caption_prob=0.5):
            log("bubble sticker -> %s %s" % (kind, tid))
    elif act == "weather":
        w = get_weather_text()
        if not w:
            return
        line = reply_with_llm(is_group, tid,
                              "[系统]主动冒泡：结合当前天气对主人说句话（下雨提醒带伞、降温提醒加衣、"
                              "好天气可以约出门）。天气：%s。一句话，12字内。" % w)
        if not line:
            return
        send_reply_chain(is_group, tid, line)
        log("bubble weather -> %s %s" % (kind, tid))
    elif act == "news":
        titles = get_news_titles(3)
        if titles:
            one = random.choice(titles)
            line = reply_with_llm(is_group, tid,
                                  "[系统]请主动冒泡：随口聊一下这条新闻，一句话12字内，别像播报：" + one)
            if not line:
                return
            send_reply_chain(is_group, tid, line,
                             force_sticker=random.random() < 0.2)
        else:
            line = reply_with_llm(is_group, tid, "[系统]主动冒泡：随口说一句你刚才在干嘛")
            if not line:
                return
            send_reply_chain(is_group, tid, line)
        log("bubble news -> %s %s" % (kind, tid))
    elif act == "care":
        line = reply_with_llm(is_group, tid,
                              "[系统]主动冒泡：主动关心一下主人，一句话，12字内，贴合当前时间段")
        if not line:
            return
        send_reply_chain(is_group, tid, line)
        log("bubble care -> %s %s" % (kind, tid))
    else:
        line = reply_with_llm(is_group, tid,
                              "[系统]主动冒泡：随口说一句你（小团子）刚才在干嘛，12字内，具体可爱")
        if not line:
            return
        send_reply_chain(is_group, tid, line)
        log("bubble status -> %s %s" % (kind, tid))

    mark_bubble(kind, tid)
    bubble_inc()
    stat("bubbles")


def bubble_worker():
    log("bubble worker start")
    time.sleep(random.uniform(120, 300))
    while True:
        try:
            try:
                maybe_write_diary()
            except Exception as e:
                log("diary trigger err: %s" % e)
            if check_events():
                time.sleep(random.uniform(90, 180))
                continue
            lo = float(CFG.get("bubble_min_min", 30))
            hi = float(CFG.get("bubble_max_min", 110))
            # 短睡轮询（每5秒检查暂停开关），保证「暂停冒泡」及时生效
            total = random.uniform(lo, hi) * 60
            waited = 0.0
            while waited < total:
                time.sleep(5)
                waited += 5
                if bubbles_paused():
                    break
            if bubbles_paused():
                log("bubble paused (flag on)")
                continue
            if today_count() >= int(CFG.get("max_bubbles_per_day", 12)):
                continue
            if not bubble_allowed_now():
                log("bubble skipped (night)")
                continue
            if not check_login():
                continue
            do_bubble()
        except Exception as e:
            log("bubble err: %s" % e)
            time.sleep(60)


def check_login():
    try:
        r = urllib.request.urlopen(NAPCAT_HTTP + "/get_login_info", timeout=3)
        return b"user_id" in r.read()
    except Exception:
        return False


# ============ WS 消息处理 ============
def strip_cq(text):
    return re.sub(r"\[CQ:[^\]]+\]", "", text or "").strip()


def msg_to_text(raw):
    """OneBot 消息 → 可读纯文本。
    数组格式（NapCat 默认）：直接提取 text 段，图片/表情等转成占位符
    （旧逻辑把整段 JSON 存进记忆，又长又乱，还夹着图片 URL）。"""
    if isinstance(raw, str):
        return strip_cq(raw)
    if not isinstance(raw, list):
        return ""
    parts = []
    for seg in raw:
        if not isinstance(seg, dict):
            continue
        tp = seg.get("type")
        d = seg.get("data") or {}
        if tp == "text":
            t = str(d.get("text") or "").strip()
            if t:
                parts.append(t)
        elif tp == "image":
            parts.append("[图片]")
        elif tp in ("face", "mface"):
            parts.append("[表情]")
        elif tp == "at":
            parts.append("[@全体成员]" if str(d.get("qq")) == "all" else "[@某人]")
        elif tp == "reply":
            parts.append("[回复]")
        elif tp == "record":
            parts.append("[语音]")
        elif tp == "video":
            parts.append("[视频]")
        elif tp == "file":
            parts.append("[文件]")
    return re.sub(r"\s+", " ", " ".join(parts)).strip()

def extract_images(raw_segs, limit=1):
    """从消息段列表提取图片（经 NapCat get_image 落盘到本地），返回路径列表"""
    out = []
    try:
        if not isinstance(raw_segs, list):
            return out
        for seg in raw_segs:
            if not isinstance(seg, dict) or seg.get("type") != "image":
                continue
            f = (seg.get("data") or {}).get("file")
            if not f:
                continue
            p = None
            try:
                r = napcat_api("get_image", {"file": f})
                p = ((r or {}).get("data") or {}).get("file")
            except Exception:
                p = None
            if p and os.path.exists(p):
                try:
                    if os.path.getsize(p) > 4 * 1024 * 1024:
                        continue
                except Exception:
                    pass
                out.append(p)
            if len(out) >= limit:
                break
    except Exception as e:
        log("extract img err: %s" % e)
    return out


def handle_event(ev):
    LAST_EVENT_TS["ts"] = time.time()   # 任何事件（含心跳帧）都刷新连接活性
    stat("events")
    try:
        pt = ev.get("post_type")
        if pt == "notice":
            if ev.get("notice_type") == "notify" and ev.get("sub_type") == "poke":
                handle_poke(ev)
            return
        if pt != "message":
            return
        mt = ev.get("message_type")
        raw = ev.get("message")
        text = msg_to_text(raw)                 # 数组/字符串消息统一转可读文本
        uid = ev.get("user_id")
        gid = ev.get("group_id")
        msg_id = ev.get("message_id")
        self_id = str(ev.get("self_id", ""))
        sender = (ev.get("sender") or {}).get("nickname", "")

        if mt == "private":
            if not CFG.get("c2c_enabled", True):
                return
            remember("users", uid, sender)
        elif mt == "group":
            if not CFG.get("group_enabled", True):
                return
            remember("groups", gid, sender)
        else:
            return

        if seen_check(msg_id):
            return
        imgs = []
        if str(CFG.get("llm_backend", "mimo")) == "local":
            imgs = extract_images(raw)
        if not text and not imgs:
            return
        if not text:
            text = "[发来了一张图片]"
        try:
            mark_processed("groups" if mt == "group" else "users",
                           gid if mt == "group" else uid, ev.get("time"))
        except Exception:
            pass

        if mt == "group":
            # @ 判断：兼容 array 格式（segment 列表）与 string 格式（CQ 码）
            at_me = False
            if isinstance(raw, list):
                for seg in raw:
                    if isinstance(seg, dict) and seg.get("type") == "at":
                        qq = str((seg.get("data") or {}).get("qq", ""))
                        if qq == self_id or qq == "all":
                            at_me = True
                            break
            else:
                s = str(raw)
                at_me = "CQ:at" in s and (self_id in s or "all" in s)
            if not at_me and ("团子" in text or "小团子" in text):
                at_me = True
            log("group msg gid=%s at_me=%s text=%s" % (gid, at_me, text[:24].replace("\n", " ")))

            # 所有群消息进小团子的"围观记忆"（免@模式的基础：它一直在听）
            mem_append("groups", gid, "user", "%s说：%s" % (sender or "群友", text))
            person_append(uid, "user", text, "群聊")   # 分人记忆：TA 在群里说的每句话都归到 TA 名下
            remember_member(gid, uid, sender, text)   # 群成员画像（不同的人不同反应）

            treat = detect_feed(text)
            if treat:
                handle_treat(True, gid, text, treat)
                return

            m_r = re.match(r"^记住[:：,，\s]*(.{2,60})$", text)
            if m_r and at_me:
                fact = m_r.group(1).strip()
                add_fact(uid, fact, pin=True, src="群聊")
                line = random.choice(["记住了喵！本喵的脑袋好使着呢", "嗯嗯，放进小本本了 📒", "好，这件事我记一辈子喵"])
                mem_append("groups", gid, "user", "%s让小团子记住：%s" % (sender or "有人", fact))
                mem_append("groups", gid, "assistant", line)
                send_reply_chain(True, gid, line)
                stat("replies")
                log("fact pinned (group %s): %s" % (gid, fact[:24]))
                return

            if handle_meme(True, gid, uid, text):
                stat("replies")
                return

            in_hot = time.time() < GROUP_HOT.get(gid, 0)
            if at_me or in_hot:
                # 被@ → 必回；热聊窗口内 → 自然接话（像真人聊起来会接着说）
                if at_me and random.random() < float(CFG.get("silent_prob", 0.01)):
                    log("group silent")
                    return
                want_voice = bool(re.search(r"发语音|语音说|来个语音|用语音|语音回", text))
                extras = []
                if want_voice:
                    extras.append("[系统]主人要求发语音：回复里输出「[语音]要说的话」（15字内口语）")
                nt = night_tired_extra(text, uid)
                if nt:
                    extras.append(nt)
                rl = try_relay_schedule(text, uid)
                if rl:
                    extras.append(rl)
                line = reply_with_llm(True, gid, "%s说：%s" % (sender or "群友", text),
                                      record=False, extra_user="\n".join(extras),
                                      person_uid=(uid if at_me else None), images=imgs)
                if not line:
                    # @ 的消息必须有回应（AI 两次都失败时的极简兜底）
                    line = random.choice(["喵？", "喵喵？", "……喵"])
                    mem_append("groups", gid, "assistant", line)
                send_reply_chain(True, gid, line)
                GROUP_HOT[gid] = time.time() + float(CFG.get("hot_window", 180))
                GROUP_LAST_REPLY[gid] = time.time()
                stat("replies")
                log("group reply %s: %s" % (gid, line[:30].replace("\n", " ")))
            else:
                # 非@消息：本地预过滤（只有疑似对话才调判断器，防 API 限流）
                suspicious = bool(re.search(r"你|您|咱|团子|猫|爸爸|妈妈|[?？]|干啥|干嘛|在吗|监视|跑了", text))
                if suspicious and time.time() - GROUP_LAST_REPLY.get(gid, 0) > float(CFG.get("interject_cooldown", 60)):
                    hist_tail = mem_load("groups", gid)[-6:]
                    recent = "\n".join((m.get("content") or "")[:40] for m in hist_tail)
                    judge = llm_chat([
                        {"role": "system",
                         "content": "你是判断器。判断最新群消息是否在对「小团子」（AI猫）说话——"
                                    "称呼它、让它做事、跟它拌嘴撒娇、说它的家人（爸爸妈妈）、"
                                    "或明显期待它回应——或者它是否非常适合自然插一句嘴。只回答 yes 或 no。"},
                        {"role": "user",
                         "content": "最近群聊：\n%s\n\n最新消息：%s" % (recent, text)}
                    ], max_tokens=5)
                    if judge and re.search(r"yes", judge, re.I):
                        line = reply_with_llm(True, gid, "%s说：%s" % (sender or "群友", text),
                                              record=False)
                        if not line:
                            line = "喵？"
                            mem_append("groups", gid, "assistant", line)
                        send_reply_chain(True, gid, line)
                        GROUP_HOT[gid] = time.time() + float(CFG.get("hot_window_interject", 120))
                        GROUP_LAST_REPLY[gid] = time.time()
                        stat("replies")
                        log("group interject %s: %s" % (gid, line[:30].replace("\n", " ")))
        else:
            if random.random() < float(CFG.get("silent_prob", 0.05)):
                log("private silent")
                return
            want_voice = bool(re.search(r"发语音|语音说|来个语音|用语音|语音回", text))
            treat = detect_feed(text)
            if treat:
                handle_treat(False, uid, text, treat)
                return

            m_r = re.match(r"^记住[:：,，\s]*(.{2,60})$", text)
            if m_r:
                fact = m_r.group(1).strip()
                add_fact(uid, fact, pin=True)
                line = random.choice(["记住了喵！本喵的脑袋好使着呢", "嗯嗯，放进小本本了 📒", "好，这件事我记一辈子喵"])
                mem_append("users", uid, "user", "（郑重地）记住：%s" % fact)
                mem_append("users", uid, "assistant", line)
                person_append(uid, "user", "（郑重地）记住：%s" % fact, "私聊")
                person_append(uid, "assistant", line, "私聊")
                send_reply_chain(False, uid, line)
                log("fact pinned (private %s): %s" % (uid, fact[:24]))
                return

            if handle_meme(False, uid, uid, text):
                stat("replies")
                return
            extras = []
            if want_voice:
                extras.append("[系统]主人要求发语音：回复里输出「[语音]要说的话」（15字内口语）")
            nt = night_tired_extra(text, uid)
            if nt:
                extras.append(nt)
            rl = try_relay_schedule(text, uid)
            if rl:
                extras.append(rl)
            line = reply_with_llm(False, uid, text, extra_user="\n".join(extras), images=imgs)
            if not line:
                # AI 两次失败时的极简兜底（不冷场）
                line = random.choice(["喵？", "喵喵？", "……喵"])
                mem_append("users", uid, "assistant", line)
            send_reply_chain(False, uid, line)
            stat("replies")
            log("private reply %s: %s" % (uid, line[:30].replace("\n", " ")))
    except Exception as e:
        log("handle err: %s" % e)


def ws_worker():
    log("ws worker start: " + NAPCAT_WS)
    ws_ref = {"ws": None}

    def keepalive():
        """每15秒发一个 action 保持入站流量（防 NapCat 踢空闲连接）"""
        while True:
            ws = ws_ref.get("ws")
            if ws:
                try:
                    ws.send(json.dumps({"action": "get_login_info", "echo": "keepalive"}))
                except Exception:
                    pass
            time.sleep(15)

    threading.Thread(target=keepalive, daemon=True).start()

    def conn_watchdog():
        """连接自检：5 分钟没收到任何事件（含心跳）→ 强制重连 WS；
        QQ 会话死亡（HTTP API 也失败）→ 写标志文件让宿主看门狗重启 NapCat。
        注意：QQ 在「等扫码」时 HTTP 也不通，但重启只会刷新二维码徒增困扰，
        因此二维码 8 分钟内更新过就视为等扫码中，不触发重启"""
        while True:
            time.sleep(120)
            silent = time.time() - LAST_EVENT_TS["ts"]
            if silent > 300:
                log("watchdog: %ds silent, forcing ws reconnect" % silent)
                LAST_EVENT_TS["ts"] = time.time()
                try:
                    ws_ref.get("ws").close()
                except Exception:
                    pass
            if not check_login():
                qr_fresh = False
                try:
                    qr_p = "/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png"
                    qr_fresh = (time.time() - os.path.getmtime(qr_p)) < 480
                except Exception:
                    pass
                if qr_fresh:
                    log("watchdog: QQ 离线但在等扫码（二维码新鲜），不重启")
                else:
                    log("watchdog: QQ session dead, flag for host restart")
                    try:
                        with open(os.path.join(BASE, "qq_dead.flag"), "w") as f:
                            f.write(time.strftime("%m-%d %H:%M"))
                    except Exception:
                        pass

    threading.Thread(target=conn_watchdog, daemon=True).start()

    def ws_on_open(w):
        ws_ref["ws"] = w
        STATS["ws_state"] = "open"
        STATS["ws_open_ts"] = time.time()
        log("ws open")
        maybe_backfill()                          # 断线补偿：回读错过的消息

    while True:
        try:
            ws = websocket.WebSocketApp(
                NAPCAT_WS,
                on_open=ws_on_open,
                # 关键：消息处理丢到独立线程，回调立即返回（否则 LLM+sleep 阻塞心跳导致 NapCat 断连）
                on_message=lambda w, m: threading.Thread(
                    target=handle_event, args=(json.loads(m),), daemon=True).start(),
                on_error=lambda w, e: log("ws err: %s" % e),
                on_close=lambda w, *a: (STATS.__setitem__("ws_state", "closed"), log("ws closed")))
            # ping_interval=0：禁用 WS ping（NapCat 不回 pong 导致 15-20s 断连），
            # 改用 keepalive 线程每 15 秒发 action 保活
            ws.run_forever(ping_interval=0)
        except Exception as e:
            log("ws crash: %s" % e)
        ws_ref["ws"] = None
        time.sleep(5)


def merge_cfg_on_boot():
    """启动写回：先重读磁盘（面板可能刚保存过新设置）→ 磁盘值优先，防旧快照覆盖"""
    try:
        _disk = load_json(CFG_PATH, {})
        merged = dict(CFG)
        if isinstance(_disk, dict) and _disk:
            merged.update(_disk)
        save_json(CFG_PATH, merged)
        CFG.update(merged)
    except Exception:
        pass


def main():
    os.makedirs(PICS, exist_ok=True)
    os.makedirs(MEM_DIR, exist_ok=True)
    os.makedirs(FLAG_DIR, exist_ok=True)
    merge_cfg_on_boot()
    log("=== tuanzi v2 start, llm_key=%s ===" % ("YES" if CFG.get("mimo_key") else "NO"))
    threading.Thread(target=status_writer, daemon=True).start()
    threading.Thread(target=flag_watcher, daemon=True).start()
    threading.Thread(target=bubble_worker, daemon=True).start()
    threading.Thread(target=weather_worker, daemon=True).start()
    threading.Thread(target=relay_worker, daemon=True).start()
    threading.Thread(target=qzone_worker, daemon=True).start()
    ws_worker()


if __name__ == "__main__":
    main()
