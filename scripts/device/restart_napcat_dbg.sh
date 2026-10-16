#!/system/bin/sh
# restart_napcat_dbg.sh — 带受限日志重启 NapCat（watchdog 自动截断，不会爆磁盘）
echo "=== 杀旧进程（/opt/QQ 通杀全部子进程：防 zygote 孤儿攥 deleted 句柄锁死磁盘，2026-10-16 事故）==="
for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep -E "/opt/QQ|Xvfb|proot" | grep -v grep | awk '{print $1}'); do kill -9 "$pid" 2>/dev/null; done
sleep 2
for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep "/opt/QQ" | grep -v grep | awk '{print $1}'); do kill -9 "$pid" 2>/dev/null; done
sleep 1
echo "left_qq: $(ps -A -o ARGS 2>/dev/null | grep -c '/opt/QQ')"
# 注：不再 rm 旧日志（nohup 的 > 重定向自带 O_TRUNC 截断且复用 inode；
# 若 rm 时恰有旧进程攥着该文件 fd，会变成 deleted 幽灵文件继续增长——10-16 事故型）

echo "=== 启动（日志到 napcat_dbg.log）==="
nohup sh /data/local/tmp/start_napcat_persist.sh > /data/local/tmp/napcat_dbg.log 2>&1 &

echo "=== watchdog：每60秒检查，仅超900KB才截断 ==="
# 2026-10-16 夜班加固：①先清理同类旧看门狗（防每次重启累积一个常驻循环）
# ②条件截断（原无条件 truncate 会把小日志撑成 900KB 空洞文件）
for _wd in $(ps -A -o PID,ARGS | grep "[n]apcat_dbg.log" | grep "[t]runcate" | awk '{print $1}'); do
  kill -9 "$_wd" 2>/dev/null
done
nohup sh -c 'while true; do sleep 60; f=/data/local/tmp/napcat_dbg.log; [ -f "$f" ] || continue; sz=$(stat -c %s "$f" 2>/dev/null || echo 0); [ "$sz" -gt 900000 ] && truncate -s 900000 "$f"; done' > /dev/null 2>&1 &
echo "=== DONE ==="
