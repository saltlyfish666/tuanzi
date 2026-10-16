#!/system/bin/sh
# start_panel.sh -- launch the visual control panel (sandbox python3, port 8898)
# Open on the tablet: http://127.0.0.1:8898
U=/data/data/com.termux/files/home/ubuntu
PROOT=/data/data/com.termux/files/usr/bin/proot
PREFIX=/data/data/com.termux/files/usr
export LD_LIBRARY_PATH=$PREFIX/lib
export PROOT_TMP_DIR=/data/local/tmp/proot-tmp
mkdir -p $PROOT_TMP_DIR
mkdir -p $U/root/tuanzi/qr

if pgrep -f "python3 /root/tuanzi/panel_server.py" > /dev/null 2>&1; then
  echo "panel already running"
  exit 0
fi

nohup sh -c "$PROOT -0 -r $U -b /dev -b /proc -b /sys -b /data/local/tmp -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root TMPDIR=/tmp /usr/bin/python3 /root/tuanzi/panel_server.py" > /data/local/tmp/panel.log 2>&1 &
sleep 2
echo "=== panel log ==="
tail -3 /data/local/tmp/panel.log 2>/dev/null
echo "=== DONE ==="
