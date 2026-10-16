#!/bin/bash
# push_v4.sh — v4 优化文件推送（root 恢复后：先跑本脚本，再 su -c 'sh /data/local/tmp/deploy_v4.sh'）
cd "$(dirname "$0")" || exit 1
export MSYS2_ARG_CONV_EXCL="*"
PKG=/data/local/tmp/tuanzi_pkg
set -e

# 预检：设备侧脚本统一 LF 换行（mksh 不接受 CRLF，本机 bash 宽容查不出，2026-10-15 踩坑）
for f in *.sh tuanzi_pkg/*.py tuanzi_pkg/*.html help.html; do
  [ -f "$f" ] || continue
  sed -i 's/\r$//' "$f"
done
echo "=== LF normalized ==="
adb push tuanzi_pkg/tuanzi_service.py $PKG/
adb push tuanzi_pkg/panel_server.py   $PKG/
adb push tuanzi_pkg/dashboard.html    $PKG/
adb push tuanzi_pkg/config.json       $PKG/
adb push help.html                    $PKG/
adb push tuanzi_pkg/pics/*.jpg        $PKG/pics/ 2>/dev/null || true
adb push start_napcat_persist.sh /data/local/tmp/
adb push start_boot_stack.sh    /data/local/tmp/
adb push tuanzi_host_agent.sh   /data/local/tmp/
adb push boot_tuanzi.sh         /data/local/tmp/
adb push ksu_boot_tuanzi.sh     /data/local/tmp/
adb push deploy_v4.sh           /data/local/tmp/
adb push start_agent.sh         /data/local/tmp/
adb push restart_agent.sh       /data/local/tmp/
adb push hot_restart_agent.sh   /data/local/tmp/
adb push hot_restart_panel.sh   /data/local/tmp/
adb push kill_stack.sh          /data/local/tmp/
adb push restart_napcat_dbg.sh  /data/local/tmp/
adb push start_llama.sh        /data/local/tmp/
adb push tuanzi_launch.sh         /data/local/tmp/
adb push vl_on.sh                 /data/local/tmp/
adb push vl_off.sh                /data/local/tmp/
echo "=== PUSH DONE ==="
