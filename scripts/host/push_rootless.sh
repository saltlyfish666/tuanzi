#!/bin/bash
# push_rootless.sh — 无root部署通道（run-as 管道直写，root丢失时使用）
# 用法: bash push_rootless.sh   （在 napcat_deploy 目录运行）
cd "$(dirname "$0")" || exit 1
H=/data/data/com.termux/files/home
B=$H/ubuntu/root/tuanzi

push_priv() {   # 推送到 Termux 私有目录（引擎文件）
  if cat "$1" | adb shell "run-as com.termux sh -c 'cat > $2'" 2>/dev/null; then
    echo "[ok] $1 -> $2"
  else
    echo "[FAIL] $1"
  fi
}
push_tmp() {    # 推送到 /data/local/tmp（宿主脚本，文件需已存在且 666）
  if cat "$1" | adb shell "run-as com.termux sh -c 'cat > $2'" 2>/dev/null; then
    echo "[ok] $1 -> $2"
  else
    echo "[FAIL] $1 (先确认目标文件已 chmod 666)"
  fi
}

echo "=== 引擎文件（私有目录）==="
push_priv tuanzi_pkg/tuanzi_service.py $B/tuanzi_service.py
push_priv tuanzi_pkg/panel_server.py   $B/panel_server.py
push_priv tuanzi_pkg/dashboard.html    $B/dashboard.html
push_priv tuanzi_pkg/config.json       $B/config.json
push_priv local_tts_cli.py             $B/local_tts_cli.py
push_priv vl_infer.py                  $B/vl_infer.py

echo "=== 宿主脚本（/data/local/tmp）==="
push_tmp start_llama.sh     /data/local/tmp/start_llama.sh
push_tmp tuanzi_launch.sh   /data/local/tmp/tuanzi_launch.sh
push_tmp vision_cycle.sh    /data/local/tmp/vision_cycle.sh
push_tmp vl_infer.py        /data/local/tmp/vl_infer.py
push_tmp tuanzi_host_agent.sh /data/local/tmp/tuanzi_host_agent.sh

echo "=== 重启（Termux 用户身份触发）==="
adb shell "run-as com.termux sh -c 'touch /data/local/tmp/tuanzi_flags/restart_engine && echo restart_flag_set'"
adb shell "run-as com.termux sh -c 'sh /data/local/tmp/hot_restart_agent.sh' 2>/dev/null" | tail -1
echo "=== 完成 ==="
