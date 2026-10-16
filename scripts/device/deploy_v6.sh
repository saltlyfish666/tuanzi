#!/system/bin/sh
U=/data/data/com.termux/files/home/ubuntu
B=$U/root/tuanzi
echo "=== 备份旧版 ==="
cp -f $B/dashboard.html $B/dashboard.html.pre_v6 2>/dev/null
cp -f $B/panel_server.py $B/panel_server.py.pre_v6 2>/dev/null
cp -f $B/tuanzi_service.py $B/tuanzi_service.py.pre_v6 2>/dev/null
cp -f $B/config.json $B/config.json.pre_v6 2>/dev/null
echo "=== 拷贝新代码 ==="
cp -f /data/local/tmp/tuanzi_pkg/dashboard.html $B/dashboard.html
cp -f /data/local/tmp/tuanzi_pkg/panel_server.py $B/panel_server.py
cp -f /data/local/tmp/tuanzi_pkg/tuanzi_service.py $B/tuanzi_service.py
cp -f /data/local/tmp/tuanzi_pkg/config_merged.json $B/config.json
echo "=== 拷贝表情包到 pics ==="
for f in /data/local/tmp/sticker_pack/*.jpg; do cp -f "$f" $B/pics/ 2>/dev/null; done
ls $B/pics/*.jpg | wc -l
echo "=== 重启面板+引擎 ==="
sh /data/local/tmp/hot_restart_panel.sh
touch /data/local/tmp/tuanzi_flags/restart_engine
echo DEPLOY_DONE
