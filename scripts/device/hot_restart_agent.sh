#!/system/bin/sh
# hot_restart_agent.sh — 热更宿主管家：同步改名副本 + 调用标准重启（2026-10-16 夜班统一）
# 旧版自含【查-杀-起】并以 agent_run.sh 名启动、无启动验证，与 restart_agent.sh 的
# 识别名不一致 -> 存在「两路径互相看不见 → 双启」隐患。现统一：
#   - 重启动作全部走 restart_agent.sh（查-杀-起-验，统一以标准名 tuanzi_host_agent.sh 运行）
#   - 本脚本只负责：把最新版同步一份到 agent_run.sh（历史兼容副本）
cp -f /data/local/tmp/tuanzi_host_agent.sh /data/local/tmp/agent_run.sh
sh /data/local/tmp/restart_agent.sh
