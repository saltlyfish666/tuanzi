#!/system/bin/sh
# tuanzi_launch_auto.sh — APK触发用（有root走su全功能，无root直接跑）
if [ -x /system/bin/su ] && /system/bin/su -c true 2>/dev/null; then
  /system/bin/su -c 'sh /data/local/tmp/tuanzi_launch.sh'
else
  sh /data/local/tmp/tuanzi_launch.sh
fi
