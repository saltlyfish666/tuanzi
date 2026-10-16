#!/system/bin/sh
# ksu_boot_tuanzi.sh — 小团子开机自启（KernelSU 原生机制）
# 安装位置: /data/adb/service.d/tuanzi_boot.sh
# KernelSU 在系统启动完成后以 ROOT 身份自动执行本目录下所有 .sh（无需 Termux:Boot 应用）
# 链路: 等系统稳定 -> 冷启动全栈（proot 守卫清理 -> zram -> NapCat -> 引擎 -> 面板 -> 宿主管家）
sleep 45
sh /data/local/tmp/start_boot_stack.sh >> /data/local/tmp/boot_stack.log 2>&1
