# -*- coding: utf-8 -*-
"""本地 Z-Image Turbo 表情包生成器 — 通过 ComfyUI API（零积分）"""
import json, sys, time, os, urllib.request, urllib.parse, random

COMFY = "http://127.0.0.1:8188"
WF_PATH = os.environ.get("COMFY_WORKFLOW", r"D:/Apps/ComfyUI/workflows/z_image_fixed_api.json")
OUT_DIR = os.environ.get("STICKER_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "sticker_out"))

def submit(prompt, seed=None):
    wf = json.load(open(WF_PATH, encoding="utf-8"))
    wf["67"]["inputs"]["text"] = prompt
    wf["70"]["inputs"]["seed"] = seed if seed is not None else random.randint(1, 2**31)
    data = json.dumps({"prompt": wf, "client_id": "tuanzi_sticker"}).encode()
    req = urllib.request.Request(COMFY + "/prompt", data=data,
                                 headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return r["prompt_id"]

def wait(pid, timeout=1200):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = json.loads(urllib.request.urlopen(COMFY + "/history/" + pid, timeout=30).read())
        if pid in h:
            return h[pid]
        time.sleep(5)
    return None

def fetch(entry, out_path):
    img = entry["outputs"]["9"]["images"][0]
    q = "filename=%s&subfolder=%s&type=%s" % (
        urllib.parse.quote(img["filename"]), img["subfolder"], img["type"])
    data = urllib.request.urlopen(COMFY + "/view?" + q, timeout=60).read()
    open(out_path, "wb").write(data)
    return out_path

def ping():
    try:
        json.loads(urllib.request.urlopen(COMFY + "/system_stats", timeout=5).read())
        return True
    except Exception:
        return False

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--ping":
        print("PING_OK" if ping() else "PING_FAIL")
        sys.exit(0)
    prompt = sys.argv[1]
    outname = sys.argv[2]
    os.makedirs(OUT_DIR, exist_ok=True)
    pid = submit(prompt)
    print("submitted:", pid, flush=True)
    entry = wait(pid)
    if not entry:
        print("TIMEOUT"); sys.exit(1)
    p = fetch(entry, os.path.join(OUT_DIR, outname))
    print("saved:", p, os.path.getsize(p), "bytes")
