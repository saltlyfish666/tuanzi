# -*- coding: utf-8 -*-
"""vl_infer.py — 视觉推理执行器（沙箱内运行，由宿主 vision_cycle.sh 调用）"""
import json
import time
import urllib.request

REQ = "/data/local/tmp/tuanzi_flags/vl_request.json"
RES = "/data/local/tmp/tuanzi_flags/vl_result.txt"
DONE = "/data/local/tmp/tuanzi_flags/vl_done"


def main():
    out = ""
    try:
        data = json.load(open(REQ, "r", encoding="utf-8"))
        payload = {
            "model": "qwen3",
            "messages": data.get("messages") or [],
            "max_tokens": int(data.get("max_tokens", 60)),
            "temperature": float(data.get("temperature", 1.0)),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            "http://127.0.0.1:8080/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode("utf-8"))
        out = (d["choices"][0]["message"]["content"] or "").strip()
        print("VL_OK %d chars" % len(out))
    except Exception as e:
        print("VL_ERR", str(e)[:150])
    with open(RES, "w", encoding="utf-8") as f:
        f.write(out or "[看图失败]")
    with open(DONE, "w") as f:
        f.write(str(time.time()))


if __name__ == "__main__":
    main()
