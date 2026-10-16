#!/system/bin/sh
# start_llama.sh — 本地大脑推理服务（v2：支持视觉模型自动优先）
export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib
export PATH=/data/data/com.termux/files/usr/bin:$PATH
U=/data/data/com.termux/files/home/ubuntu
PR="/data/data/com.termux/files/usr/bin/proot -0 -r $U -b /dev -b /proc -b /sys -b /data/local/tmp -w /root"
ENV="/usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root TMPDIR=/tmp"
if ps -A -o ARGS 2>/dev/null | grep -q "llama-[s]erver"; then
  echo "LLAMA_ALREADY_RUNNING"
  exit 0
fi
M="$U/root/models"
MODEL=""; MMPROJ=""
# 大脑选择 v5（2026-10-16）：读 config.json 的 local_model（面板可切换），缺省走首选链
LM=$(/data/data/com.termux/files/usr/bin/awk -F'"' '/"local_model"/{print $4}' "$U/root/tuanzi/config.json" 2>/dev/null)
case "$LM" in
  qwen2.5-vl-3b) MODEL="Qwen2.5-VL-3B-Q4_K_M.gguf"; MMPROJ="mmproj-Qwen2.5-VL-3B-Q8.gguf";;
  qwen3-1.7b)    MODEL="Qwen3-1.7B-Q4_K_M.gguf";;
  qwen3-0.6b)    MODEL="Qwen3-0.6B-Q4_K_M.gguf";;
  qwen3-vl-4b)   MODEL="Qwen3-VL-4B-Q4_K_M.gguf"; MMPROJ="mmproj-Qwen3-VL-4B-F16.gguf";;
  *)
    if [ -f "$M/Qwen2.5-VL-3B-Q4_K_M.gguf" ] && [ -f "$M/mmproj-Qwen2.5-VL-3B-Q8.gguf" ]; then
      MODEL="Qwen2.5-VL-3B-Q4_K_M.gguf"; MMPROJ="mmproj-Qwen2.5-VL-3B-Q8.gguf"
    elif [ -f "$M/Qwen3-1.7B-Q4_K_M.gguf" ]; then
      MODEL="Qwen3-1.7B-Q4_K_M.gguf"
    elif [ -f "$M/Qwen3-0.6B-Q4_K_M.gguf" ]; then
      MODEL="Qwen3-0.6B-Q4_K_M.gguf"
    else
      echo "NO_MODEL_FOUND"; exit 1
    fi
  ;;
esac
[ -f "$M/$MODEL" ] || { echo "MODEL_FILE_MISSING $MODEL"; exit 1; }
echo "MODEL=$MODEL MMPROJ=$MMPROJ"
echo "$MODEL" > /data/local/tmp/llama_model.txt 2>/dev/null || true
if [ -n "$MMPROJ" ]; then
  nohup $PR $ENV bash -c "/root/src/llama.cpp-b11681/build/bin/llama-server -m /root/models/$MODEL --mmproj /root/models/$MMPROJ --host 127.0.0.1 --port 8080 -c 3072 -t 6 --jinja" > /data/local/tmp/llama_server.log 2>&1 &
else
  nohup $PR $ENV bash -c "/root/src/llama.cpp-b11681/build/bin/llama-server -m /root/models/$MODEL --host 127.0.0.1 --port 8080 -c 3072 -t 6 --jinja" > /data/local/tmp/llama_server.log 2>&1 &
fi
echo "LLAMA_STARTED pid=$!"
