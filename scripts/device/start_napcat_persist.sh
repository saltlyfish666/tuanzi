#!/system/bin/sh
# start_napcat_persist.sh — 常驻启动 NapCat（proot Ubuntu + Xvfb + QQ）
# v3 (2026-10-15 内存瘦身版):
#   - Xvfb 分辨率 1080x2400 -> 720x1280（帧缓冲 10.4MB->3.7MB，Chromium 后备存储同比缩小）
#   - QQ 加 Chromium 内存 Flags：--disable-gpu（Xvfb 无 GPU，省一个 GPU 进程）
#     --disable-dev-shm-usage（/dev/shm 是 tmpfs=吃内存！改走 /tmp 磁盘）
#   - 保留 /dev/shm 重建（Electron 启动时仍会探测）

# 重建 /dev/shm（重启后必做；--disable-dev-shm-usage 后用量大减，但保留无妨）
mkdir -p /dev/shm && chmod 1777 /dev/shm 2>/dev/null

export PREFIX=/data/data/com.termux/files/usr
export HOME_TERMUX=/data/data/com.termux/files/home
export PATH=$PREFIX/bin:$PATH
export LD_LIBRARY_PATH=$PREFIX/lib
export PROOT_TMP_DIR=/data/local/tmp/proot-tmp
mkdir -p $PROOT_TMP_DIR

PROOT_CMD="$PREFIX/bin/proot -0 -r $HOME_TERMUX/ubuntu -b /dev -b /proc -b /sys -b /data/local/tmp -w /root"
GPATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# v4 瘦身 flags；若曾因 flags 起不来被看门狗降级（marker 存在），退回纯净模式
if [ -f /data/local/tmp/.napcat_plain_flags ]; then
  QQ_FLAGS="--no-sandbox"
  echo "=== plain flags mode (fallback) ==="
else
  QQ_FLAGS="--no-sandbox --disable-gpu --disable-dev-shm-usage"
fi

echo "=== NapCat 常驻启动（v3 瘦身）$(date) ==="
$PROOT_CMD /usr/bin/env PATH="$GPATH" /bin/sh -c "
  export DISPLAY=:99
  if ! pgrep -x Xvfb > /dev/null 2>&1; then
    Xvfb :99 -screen 0 720x1280x24 > /tmp/xvfb.log 2>&1 &
    sleep 3
  fi
  echo 'Xvfb:' \$(pgrep -x Xvfb || echo none)
  cd /opt/QQ
  echo '启动 QQ...'
  exec ./qq $QQ_FLAGS
"
