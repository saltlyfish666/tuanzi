#!/system/bin/sh
# start_agent.sh — 确保宿主管家存活（幂等：已在运行则不动；2026-10-16 夜班加固）
# 双名识别（标准名 tuanzi_host_agent.sh + 改名副本 agent_run.sh），
# 防"看不见已在跑的管家 -> 双启"。调用方（pause_tuanzi.sh 自动恢复等）不解析输出。
list_agents() {
  ps -A -o PID,ARGS | grep -E "tuanzi_host_agent\.sh|agent_run\.sh" | grep -v "grep -E" | awk '{print $1}'
}
EXIST=$(list_agents | tr '\n' ' ')
if [ -n "$EXIST" ]; then
  echo "AGENT_ALREADY_UP pid=[$EXIST]"
  exit 0
fi
nohup sh /data/local/tmp/tuanzi_host_agent.sh >/dev/null 2>&1 &
sleep 2
NEW=$(list_agents | tr '\n' ' ')
if [ -n "$NEW" ]; then
  echo "AGENT_UP pid=[$NEW]"
else
  echo "AGENT_FAIL"
fi
