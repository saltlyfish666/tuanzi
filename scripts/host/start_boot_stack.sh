#!/system/bin/sh
# start_boot_stack.sh -- 小团子全栈冷启动（平板重启后由 Termux:Boot 调用，也可手动跑）
# 链路: NapCat(proot+Xvfb+QQ) -> 等端口就绪 -> 引擎 -> 面板 -> 宿主管家(root常驻)
U=/data/data/com.termux/files/home/ubuntu
PREFIX=/data/data/com.termux/files/usr
LOG=/data/local/tmp/boot_stack.log
say() { echo "[$(date '+%m-%d %H:%M:%S')] $1" >> $LOG; }

say "=== boot stack start ==="

# 0. proot 共享状态守卫清理（2026-10-15 事故复盘：kill -9 屠杀 proot 后
#    PROOT_TMP_DIR 共享状态错乱 -> socket 仿真故障：幻影 EADDRINUSE、
#    bind 成功但不服务、python 空转 73% CPU。冷启动前无 proot 运行时清理最安全）
if pgrep -f "[p]root" > /dev/null 2>&1; then
  say "WARN: proot still running, skip state cleanup"
else
  rm -rf /data/local/tmp/proot-tmp/* 2>/dev/null
  mkdir -p /data/local/tmp/proot-tmp
  say "proot state cleaned"
fi

# 0b. zram 减压阀（内核有 /dev/block/zram0 且当前无任何 swap 时，挂 512MB 压缩交换）
#     7.4G 内存的平板跑 Electron+QQ+3个python，内存压力是真实风险；
#     zram 不占磁盘、压缩比 ~2-3 倍，是"别再爆内存"的最后一道保险
#     注意：/proc/swaps 永远有表头行，"无 swap" = 行数 <= 1
SWAPLINES=$(wc -l < /proc/swaps 2>/dev/null || echo 0)
if [ -b /dev/block/zram0 ] && [ "$SWAPLINES" -le 1 ]; then
  echo 536870912 > /sys/block/zram0/disksize 2>/dev/null
  mkswap /dev/block/zram0 >/dev/null 2>&1
  if swapon /dev/block/zram0 2>/dev/null; then
    say "zram 512M swap ON"
  else
    say "zram swapon failed (non-fatal)"
  fi
else
  say "zram skip (no device or swap already active)"
fi

# 1. NapCat（start_napcat_persist.sh 内部 exec qq 会前台阻塞，必须放后台）
if pgrep -f "qq --no-sandbox" > /dev/null 2>&1; then
  say "NapCat already running, skip"
else
  nohup sh /data/local/tmp/start_napcat_persist.sh > /data/local/tmp/napcat_boot.log 2>&1 &
  say "NapCat launching (proot+Xvfb+QQ)..."
  # 日志截断看门狗（2026-10-15 事故：QQ 扫码等待期以 ~4.3MB/s 喷 hex 日志，
  # 7 分钟写满 1.8GB、40 分钟吃光 84GB 磁盘把系统搞崩重启。
  # restart_napcat_dbg.sh 有看门狗但冷启动路径没有 -> 必须补上）
  # 2026-10-16 夜班加固：①先清理同类旧看门狗（原先每次冷启动/重启都累积一个
  #   常驻 while 循环，实测已积累 2 个）②条件截断（原无条件 truncate 会把小
  #   日志撑成 800KB 空洞文件——实测 36KB 真实日志被撑到 800KB，tail 读到 NUL）
  for _wd in $(ps -A -o PID,ARGS | grep "[n]apcat_boot.log" | grep "[t]runcate" | awk '{print $1}'); do
    kill -9 "$_wd" 2>/dev/null
  done
  nohup sh -c 'while true; do sleep 30; f=/data/local/tmp/napcat_boot.log; [ -f "$f" ] || continue; sz=$(stat -c %s "$f" 2>/dev/null || echo 0); [ "$sz" -gt 800000 ] && truncate -s 800000 "$f"; done' > /dev/null 2>&1 &
  # QQ flags 降级看门狗：v4 瘦身 flags 若导致 QQ 起不来（45s 后进程不在），
  # 自动切纯净 flags 重启一次（marker 持久化，不会循环重启）
  nohup sh -c 'sleep 45; if ! pgrep -f "qq --no-[s]andbox" >/dev/null 2>&1; then echo "[$(date "+%m-%d %H:%M:%S")] qq dead after slim flags -> fallback plain" >> /data/local/tmp/boot_stack.log; touch /data/local/tmp/.napcat_plain_flags; for p in $(ps -A -o PID,ARGS | grep -E "/opt/QQ|start_napcat_[p]ersist" | grep -v grep | awk "{print \$1}"); do kill -9 $p 2>/dev/null; done; nohup sh /data/local/tmp/start_napcat_persist.sh >> /data/local/tmp/napcat_boot.log 2>&1 & fi' > /dev/null 2>&1 &
fi

# 2. 等 NapCat HTTP 3000 就绪（QQ+Electron 冷启约 20-40s，最多等 80s）
i=0
up=0
while [ $i -lt 40 ]; do
  if "$PREFIX/bin/curl" -s -m 2 -o /dev/null http://127.0.0.1:3000 2>/dev/null; then
    say "NapCat port 3000 up (waited $((i*2))s)"
    up=1
    break
  fi
  i=$((i+1))
  sleep 2
done
[ $up -eq 0 ] && say "WARN: NapCat 3000 not up after 80s (QQ 大概率在等扫码，面板 :8898/qr/ 有二维码)"

# 3. 引擎（内部会先杀旧实例；冷启时没有旧实例，无副作用）
sh /data/local/tmp/start_tuanzi.sh >> $LOG 2>&1
say "engine started"

# 4. 面板（:8898）
sh /data/local/tmp/start_panel.sh >> $LOG 2>&1
say "panel started"

# 5. 宿主管家（root 常驻：保活 + flag 自愈）
# 双名识别（tuanzi_host_agent.sh 标准名 + agent_run.sh 旧名）：防止管家以改名
# 副本运行时被误判为"不在" -> 双启两份管家（2026-10-16 夜班统一）
AG_HIT=$(ps -A -o ARGS | grep -E "tuanzi_host_agent\.sh|agent_run\.sh" | grep -v "grep -E")
if [ -z "$AG_HIT" ]; then
  nohup sh /data/local/tmp/tuanzi_host_agent.sh > /dev/null 2>&1 &
  say "host agent started"
else
  say "host agent already running"
fi

say "=== boot stack done ==="
