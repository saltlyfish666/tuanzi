#!/system/bin/sh
# tuanzi_host_agent.sh -- host-side keeper v2 (runs as ROOT via su)
# Responsibilities:
#   1. heartbeat (panel liveness)
#   2. qq_dead.flag (written by engine conn_watchdog) -> restart NapCat + engine
#   3. panel manual actions: restart_napcat / restart_engine flags
#   4. keep panel_server alive (heartbeat stale + process gone -> restart)
#   5. sync NapCat login qrcode into sandbox qr dir (panel shows it)
# NOTE: restart_napcat_dbg.sh kills ALL proot processes (NapCat itself runs
#       under proot), so the engine and panel die too -> must restart both.
U=/data/data/com.termux/files/home/ubuntu
FLAGDIR=/data/local/tmp/tuanzi_flags
QQDEAD=$U/root/tuanzi/qq_dead.flag
QQQR_SRC=$U/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png
QQQR_DST=$U/root/tuanzi/qr/napcat_qr.png
LOG=/data/local/tmp/host_agent.log
GRACE=/data/local/tmp/.agent_grace_until   # after our own restart, ignore stale flags

mkdir -p $FLAGDIR
mkdir -p $U/root/tuanzi/qr
echo "[$(date '+%m-%d %H:%M:%S')] host agent start pid=$$" >> $LOG

# ---- 日志轮转：超 5MB 切档保留 3 份（两个 log() 都是每次现开文件，mv 安全）----
CNT=0
rotate_log() {
  f="$1"
  [ -f "$f" ] || return 0
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  [ "$sz" -gt 5242880 ] || return 0
  rm -f "$f.3" 2>/dev/null
  [ -f "$f.2" ] && mv -f "$f.2" "$f.3" 2>/dev/null
  [ -f "$f.1" ] && mv -f "$f.1" "$f.2" 2>/dev/null
  mv -f "$f" "$f.1" 2>/dev/null
  : > "$f" 2>/dev/null
  echo "[$(date '+%m-%d %H:%M:%S')] rotated $f ($sz bytes)" >> "$f"
}

# ★ 长连接写入的日志禁止轮转（mv 后写入者 fd 仍指向改名文件，看门狗只盯原路径
#   -> 孤儿文件无限增长。2026-10-16 事故：napcat_boot.log.1 长到 99.7GB 差点撑爆磁盘）
#   这类日志只允许「就地截断」，并且加一道大文件保险丝兜底。
cap_log() {
  f="$1"; cap="$2"
  [ -f "$f" ] || return 0
  sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
  [ "$sz" -gt "$cap" ] && truncate -s "$cap" "$f" 2>/dev/null
}

big_guard() {
  for f in /data/local/tmp/*.log /data/local/tmp/*.log.* /data/local/tmp/*.log.1; do
    [ -f "$f" ] || continue
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    if [ "$sz" -gt 524288000 ]; then
      echo "[$(date '+%m-%d %H:%M:%S')] BIG_GUARD: $f $sz bytes -> truncate" >> $LOG
      truncate -s 800000 "$f" 2>/dev/null
    fi
  done
}

# ---- 内存看门狗（v4 防爆内存）：MemAvailable 分级处置 ----
# <600MB: 清 /dev/shm 陈旧文件 + QQ/NapCat 旧日志（软处置，不打断服务）
# <400MB 且连续 2 次: 重启 NapCat+引擎（最大户，日志留证）
LOW_MEM_STREAK=0
mem_avail_mb() {
  awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo 2>/dev/null
}
mem_watchdog() {
  avail=$(mem_avail_mb)
  [ -n "$avail" ] || return 0
  if [ "$avail" -lt 600 ]; then
    echo "[$(date '+%m-%d %H:%M:%S')] mem low: ${avail}MB -> soft cleanup" >> $LOG
    find /dev/shm -type f -mmin +10 -delete 2>/dev/null
    find $U/opt/QQ/resources/app/app_launcher/napcat/logs -name "*.log" -mtime +1 -delete 2>/dev/null
    find $U/opt/QQ/logs -name "*.log" -mtime +1 -delete 2>/dev/null
  fi
  if [ "$avail" -lt 400 ]; then
    LOW_MEM_STREAK=$((LOW_MEM_STREAK+1))
    if [ $LOW_MEM_STREAK -ge 2 ] && ! in_grace; then
      echo "[$(date '+%m-%d %H:%M:%S')] mem CRITICAL: ${avail}MB x2 -> restart napcat+engine" >> $LOG
      LOW_MEM_STREAK=0
      restart_napcat_and_engine
    fi
  else
    LOW_MEM_STREAK=0
  fi
}

# ---- 每日日志清理（不管大小，1 天前的 NapCat/QQ 内部日志都清）----
daily_clean_ts() {
  [ -f /data/local/tmp/.daily_clean_ts ] && cat /data/local/tmp/.daily_clean_ts 2>/dev/null || echo 0
}

restart_napcat_and_engine() {
  echo "[$(date '+%m-%d %H:%M:%S')] restart NapCat..." >> $LOG
  sh /data/local/tmp/restart_napcat_dbg.sh >> $LOG 2>&1
  sleep 10
  echo "[$(date '+%m-%d %H:%M:%S')] NapCat up, restart engine..." >> $LOG
  sh /data/local/tmp/start_tuanzi.sh >> $LOG 2>&1
  # grace window: engine watchdog may have written a stale qq_dead.flag
  # during the restart window -> ignore flags for 90s
  echo $(( $(date +%s) + 90 )) > $GRACE
  rm -f $QQDEAD
  echo "[$(date '+%m-%d %H:%M:%S')] napcat+engine restarted, grace 90s" >> $LOG
}

in_grace() {
  if [ -f $GRACE ]; then
    until=$(cat $GRACE 2>/dev/null)
    now=$(date +%s)
    [ -n "$until" ] && [ "$now" -lt "$until" ] && return 0
  fi
  return 1
}

while true; do
  touch $FLAGDIR/agent_heartbeat

  # --- log rotation + mem watchdog + daily clean (every ~60s = 20 loops x 3s) ---
  CNT=$((CNT+1))
  if [ $((CNT % 20)) -eq 0 ]; then
    rotate_log /data/local/tmp/host_agent.log
    rotate_log $U/root/tuanzi/service.log
    rotate_log $U/root/tuanzi/panel.log
    # /data/local/tmp/panel.log 是 start_panel.sh 的 nohup 重定向（长连接写入）→ 禁止 mv，只能就地截断
    # （同 10-16 事故型：mv 后写入者 fd 跟随改名文件无限增长，看门狗只盯原路径）
    cap_log /data/local/tmp/panel.log 800000
    cap_log /data/local/tmp/napcat_boot.log 800000
    cap_log /data/local/tmp/napcat_dbg.log 900000
    big_guard
    # QQ 孤儿守卫：主进程不在但 /opt/QQ 子进程还活着 -> 杀（zygote 孤儿会攥着
    # deleted 文件句柄锁死磁盘空间，2026-10-16 事故的根因）
    if ! pgrep -f "qq --no-sandbox" > /dev/null 2>&1; then
      for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep "/opt/QQ" | grep -v grep | awk '{print $1}'); do
        kill -9 "$pid" 2>/dev/null
        echo "[$(date '+%m-%d %H:%M:%S')] QQ orphan killed pid=$pid" >> $LOG
      done
    fi
    # 本地大脑（llama-server）保活：不在就拉起（模型存在时；2026-10-16 本地化）
    if ! ps -A -o ARGS 2>/dev/null | grep -q "llama-[s]erver"; then
      if [ -f "$U/root/models/Qwen3-1.7B-Q4_K_M.gguf" ] || [ -f "$U/root/models/Qwen3-0.6B-Q4_K_M.gguf" ]; then
        echo "[$(date '+%m-%d %H:%M:%S')] llama-server dead -> start" >> $LOG
        sh /data/local/tmp/start_llama.sh >> $LOG 2>&1
      fi
    fi
    NAPLOG=$U/opt/QQ/resources/app/app_launcher/napcat/logs
    if [ -d "$NAPLOG" ]; then
      dsz=$(du -sm "$NAPLOG" 2>/dev/null | cut -f1)
      if [ -n "$dsz" ] && [ "$dsz" -gt 30 ]; then
        find "$NAPLOG" -name "*.log" -mtime +2 -delete 2>/dev/null
        echo "[$(date '+%m-%d %H:%M:%S')] napcat logs ${dsz}MB -> cleaned >2d old" >> $LOG
      fi
    fi
    mem_watchdog
    # 每日清理（距上次超过 20 小时就跑一次）
    NOW=$(date +%s)
    LASTCLEAN=$(daily_clean_ts)
    if [ $((NOW - LASTCLEAN)) -gt 72000 ]; then
      find $U/opt/QQ/resources/app/app_launcher/napcat/logs -name "*.log" -mtime +1 -delete 2>/dev/null
      find $U/opt/QQ/logs -name "*.log" -mtime +1 -delete 2>/dev/null
      echo $NOW > /data/local/tmp/.daily_clean_ts
      echo "[$(date '+%m-%d %H:%M:%S')] daily log clean done" >> $LOG
    fi
  fi

  # --- QQ session dead (engine watchdog) ---
  if [ -f $QQDEAD ]; then
    if in_grace; then
      echo "[$(date '+%m-%d %H:%M:%S')] qq_dead.flag ignored (grace)" >> $LOG
      rm -f $QQDEAD
    else
      echo "[$(date '+%m-%d %H:%M:%S')] qq_dead.flag -> restart" >> $LOG
      restart_napcat_and_engine
    fi
  fi

  # --- panel: restart NapCat ---
  if [ -f $FLAGDIR/restart_napcat ]; then
    echo "[$(date '+%m-%d %H:%M:%S')] panel -> restart napcat" >> $LOG
    restart_napcat_and_engine
    rm -f $FLAGDIR/restart_napcat
  fi

  # --- panel: restart engine only ---
  if [ -f $FLAGDIR/restart_engine ]; then
    echo "[$(date '+%m-%d %H:%M:%S')] panel -> restart engine" >> $LOG
    sh /data/local/tmp/start_tuanzi.sh >> $LOG 2>&1
    rm -f $FLAGDIR/restart_engine
  fi

  # --- 重载本地大脑（面板切换模型时触发）2026-10-16 ---
  if [ -f $FLAGDIR/restart_llama ]; then
    rm -f $FLAGDIR/restart_llama
    echo "[$(date '+%m-%d %H:%M:%S')] restart_llama -> kill+start" >> $LOG
    ( for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep -E "llama-[s]erver|[p]root.*llama" | awk '{print $1}'); do kill -9 "$pid" 2>/dev/null; done
      sleep 2
      sh /data/local/tmp/start_llama.sh ) >> $LOG 2>&1 &
  fi

  # --- 视觉请求（引擎下单 → 宿主执行切VL+推理+切回）2026-10-16 ---
  if [ -f $FLAGDIR/vl_request.json ] && [ ! -f $FLAGDIR/vl_running ]; then
    echo "[$(date '+%m-%d %H:%M:%S')] vl request -> processing" >> $LOG
    ( sh /data/local/tmp/vision_cycle.sh ) > /dev/null 2>&1 &
  fi

  # --- keep engine alive (v4 补缺口：引擎主线程死了没有任何人拉它) ---
  # ws_worker 跑在主线程，未捕获异常会整个杀死引擎进程 -> 必须进程级保活
  if ! pgrep -f "python3 /root/tuanzi/tuanzi_service.py" > /dev/null 2>&1; then
    NOW=$(date +%s)
    LASTE=0
    [ -f /data/local/tmp/.engine_restart_ts ] && LASTE=$(cat /data/local/tmp/.engine_restart_ts 2>/dev/null)
    if [ $((NOW - LASTE)) -ge 30 ]; then
      echo "[$(date '+%m-%d %H:%M:%S')] engine dead -> start" >> $LOG
      echo $NOW > /data/local/tmp/.engine_restart_ts
      sh /data/local/tmp/start_tuanzi.sh >> $LOG 2>&1
    fi
  fi

  # --- keep panel alive (process-level + PORT-level check + 30s anti-storm cooldown) ---
  # 2026-10-15 事故复盘：proot 状态错乱时进程活着但端口不服务（73% CPU 空转），
  # 纯 pgrep 保活是瞎子 -> 必须加端口探测（curl 127.0.0.1:8898）
  CURL=/data/data/com.termux/files/usr/bin/curl
  panel_port_ok() {
    [ -x "$CURL" ] || return 0
    "$CURL" -s -m 4 -o /dev/null http://127.0.0.1:8898/ 2>/dev/null
  }
  if pgrep -f "python3 /root/tuanzi/panel_server.py" > /dev/null 2>&1; then
    if ! panel_port_ok; then
      # 进程在但端口不服务 -> 杀进程（含 proot 包装）让保活重建
      echo "[$(date '+%m-%d %H:%M:%S')] panel alive but port dead -> kill" >> $LOG
      for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep "[p]anel_server.py" | awk '{print $1}'); do
        kill -9 "$pid" 2>/dev/null
      done
    fi
  else
    NOW=$(date +%s)
    LAST=0
    [ -f /data/local/tmp/.panel_restart_ts ] && LAST=$(cat /data/local/tmp/.panel_restart_ts 2>/dev/null)
    if [ $((NOW - LAST)) -ge 30 ]; then
      echo "[$(date '+%m-%d %H:%M:%S')] panel dead -> start" >> $LOG
      echo $NOW > /data/local/tmp/.panel_restart_ts
      sh /data/local/tmp/start_panel.sh >> $LOG 2>&1
    fi
  fi

  # --- sync login qrcode for panel display ---
  if [ -f $QQQR_SRC ]; then
    if [ ! -f $QQQR_DST ] || [ $QQQR_SRC -nt $QQQR_DST ]; then
      cp -f $QQQR_SRC $QQQR_DST 2>/dev/null
      chmod 644 $QQQR_DST 2>/dev/null
      echo "[$(date '+%m-%d %H:%M:%S')] qrcode synced to panel" >> $LOG
    fi
  fi

  sleep 3
done
