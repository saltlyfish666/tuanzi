#!/system/bin/sh
# tuanzi_launch.sh — 小团子一键启动（智能补齐：缺什么补什么，已在跑的跳过）
export LD_LIBRARY_PATH=/data/data/com.termux/files/usr/lib
export PATH=/data/data/com.termux/files/usr/bin:$PATH
U=/data/data/com.termux/files/home/ubuntu
echo "======== 小团子一键启动 ========"
echo "- 检查 NapCat(QQ)..."
if ! pgrep -f "qq --no-sandbox" > /dev/null 2>&1; then
  sh /data/local/tmp/start_napcat_persist.sh > /dev/null 2>&1 &
  echo "  NapCat 启动中（登录可能需要扫码）"
else
  echo "  NapCat 已在运行"
fi
sleep 2
echo "- 检查 引擎..."
if ! ps -A -o ARGS 2>/dev/null | grep -q "tuanzi_service.py"; then
  sh /data/local/tmp/start_tuanzi.sh > /dev/null 2>&1
  echo "  引擎已启动"
else
  echo "  引擎已在运行"
fi
echo "- 检查 面板..."
if ! ps -A -o ARGS 2>/dev/null | grep -q "panel_server.py"; then
  sh /data/local/tmp/start_panel.sh > /dev/null 2>&1
  echo "  面板已启动"
else
  echo "  面板已在运行"
fi
echo "- 检查 管家..."
if ! ps -A -o ARGS 2>/dev/null | grep -q "tuanzi_host_agent.sh"; then
  sh /data/local/tmp/start_agent.sh > /dev/null 2>&1
  echo "  管家已启动"
else
  echo "  管家已在运行"
fi
echo "- 检查 本地大脑(llama-server)..."
sh /data/local/tmp/start_llama.sh
echo "- 检查 宿主管家心跳..."
sleep 2
echo "======== 启动完成 ========"
echo "面板地址：http://127.0.0.1:8898"
echo "（QQ 若提示扫码，请打开面板首页扫码登录）"
