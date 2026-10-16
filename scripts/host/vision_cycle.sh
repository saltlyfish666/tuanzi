#!/system/bin/sh
# vision_cycle.sh — 视觉处理完整周期（宿主执行）：切VL → 推理 → 切回日常
export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib
export PATH=/data/data/com.termux/files/usr/bin:$PATH
U=/data/data/com.termux/files/home/ubuntu
PR="/data/data/com.termux/files/usr/bin/proot -0 -r $U -b /dev -b /proc -b /sys -b /data/local/tmp -w /root"
ENV="/usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root TMPDIR=/tmp"
F=/data/local/tmp/tuanzi_flags
[ -f $F/vl_running ] && exit 0
touch $F/vl_running
rm -f $F/vl_result.txt $F/vl_done
# 1) 停日常大脑
for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep -E "llama-[s]erver|[p]root.*llama" | awk '{print $1}'); do kill -9 "$pid" 2>/dev/null; done
sleep 2
# 2) 起视觉大脑
nohup $PR $ENV bash -c "/root/src/llama.cpp-b11681/build/bin/llama-server -m /root/models/Qwen3-VL-4B-Q4_K_M.gguf --mmproj /root/models/mmproj-Qwen3-VL-4B-F16.gguf --host 127.0.0.1 --port 8080 -c 2048 -t 6 --jinja" > /data/local/tmp/llama_server.log 2>&1 &
i=0
while [ $i -lt 40 ]; do
  code=$(env -u LD_LIBRARY_PATH /data/data/com.termux/files/usr/bin/curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/health 2>/dev/null)
  [ "$code" = "200" ] && break
  sleep 2; i=$((i+1))
done
# 3) 视觉推理（沙箱内执行）
$PR $ENV /usr/bin/python3 /root/tuanzi/vl_infer.py >> /data/local/tmp/llama_server.log 2>&1
# 4) 切回日常
for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep -E "llama-[s]erver|[p]root.*llama" | awk '{print $1}'); do kill -9 "$pid" 2>/dev/null; done
sleep 2
sh /data/local/tmp/start_llama.sh >> /data/local/tmp/llama_server.log 2>&1
sleep 3
[ -f $F/vl_done ] || touch $F/vl_done
rm -f $F/vl_running $F/vl_request.json
echo "VISION_CYCLE_DONE"
