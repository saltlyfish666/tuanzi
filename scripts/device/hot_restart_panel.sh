#!/system/bin/sh
# hot_restart_panel.sh - 热更面板：拷贝新 panel_server.py 并重启（2026-10-16 夜班加固：+启动验证）
# 注意：拷贝动作与"调用本脚本"务必分两条命令执行——若同一条命令的文本里带 panel_server.py
# 明文，外层 shell 会被下面的杀进程循环误杀（03:08 踩过，exit 137）
U=/data/data/com.termux/files/home/ubuntu
cp -f /data/local/tmp/tuanzi_pkg/panel_server.py $U/root/tuanzi/panel_server.py
for pid in $(ps -A -o PID,ARGS | grep "[p]anel_server.py" | awk '{print $1}'); do
  kill -9 "$pid" 2>/dev/null
done
sleep 2
sh /data/local/tmp/start_panel.sh
sleep 3
if pgrep -f "python3 /root/tuanzi/panel_server.py" >/dev/null 2>&1; then
  echo "PANEL_UP (process alive)"
else
  echo "PANEL_FAIL"
fi
