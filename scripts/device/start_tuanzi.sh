#!/system/bin/sh
# start_tuanzi.sh — 启动小团子活人感引擎
U=/data/data/com.termux/files/home/ubuntu
PROOT=/data/data/com.termux/files/usr/bin/proot
PREFIX=/data/data/com.termux/files/usr
export LD_LIBRARY_PATH=$PREFIX/lib
export PROOT_TMP_DIR=/data/local/tmp/proot-tmp

echo "=== 杀旧实例 ==="
for pid in $(ps -A -o PID,ARGS 2>/dev/null | grep tuanzi_service | grep -v grep | awk '{print $1}'); do
  kill -9 $pid 2>/dev/null
done
sleep 1

echo "=== 启动引擎 ==="
nohup sh -c "$PROOT -0 -r $U -b /dev -b /proc -b /sys -b /data/local/tmp -w /root /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root TMPDIR=/tmp /usr/bin/python3 /root/tuanzi/tuanzi_service.py" > /dev/null 2>&1 &
sleep 8

echo "=== 服务日志 ==="
tail -8 $U/root/tuanzi/service.log 2>/dev/null
echo "=== DONE ==="
