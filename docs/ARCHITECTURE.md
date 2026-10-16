# 架构说明

## 进程模型

| 进程 | 语言 | 职责 |
|---|---|---|
| `tuanzi_service.py` | Python | 引擎：WebSocket 收 QQ 事件、调度大脑、发送回复、所有后台 worker |
| `panel_server.py` | Python | 面板后端：聚合状态 API + 面板静态页 + 操作 API |
| `napcat` | Node/Electron | QQ 协议实现（OneBot v11），暴露 WS(3001) + HTTP(3000) |
| `llama-server` | C++ | 本地大脑（可选，OpenAI 兼容接口，端口 8080） |
| `tuanzi_host_agent.sh` | Shell | 宿主管家：3 秒巡更，保活/重启/轮转/看门狗 |

## 通信拓扑

```
QQ 服务器 ──► NapCat ──(WS 事件)──► 引擎
                ▲                      │
                └────(HTTP 发送)◄──────┘
面板浏览器 ──(HTTP 5s 轮询)──► panel_server ──(读状态文件/flag)──► 沙箱共享目录
```

- 引擎与控制面通过**共享目录文件**解耦：`flags/`(指令)/`status.json`(状态)/`*.log`(日志)
- 面板所有"操作" = 写 flag 文件；管家看到 flag 执行动作（重启/热更/切换）
- 这让"网页遥控一台平板"在零数据库、零消息队列的前提下成立

## 大脑抽象

- 统一 `llm_chat(messages)` 接口，`config.json: llm_backend` 白名单切换 `local|云端`
- 本地：llama-server `/v1/chat/completions`；云端：任意 OpenAI 兼容 API
- 视觉：本地多模态 GGUF 直接看图；纯文本本地模型则自动切换视觉大模型处理图片后再切回（`vision_cycle.sh`）
- 推理型云模型注意给足 `max_tokens`（思考 token 与回答共享预算）

## 引擎内部（主要 worker）

- `ws_worker`：事件主循环（消息去重、戳一戳、通知处理）
- `bubble_worker`：主动冒泡/事件冒泡（节日、早晚安、天气突变），受夜间免打扰与每日上限约束
- `relay_worker`：定时转达任务（自然语言提取 → 排期 → 到点发送）
- `qzone_worker`：QQ 空间动态关注
- `weather_worker`：天气轮询（20 分钟续期）
- `flag_watcher`：5 秒轮询 flags；`status_writer`：10 秒写状态

## 记忆系统

- 会话流：`memory/users_<uid>.json`、`memory/groups_<gid>.json`
- 分人档案：`memory/persons/<uid>.json`（跨场合时间线 + 长期事实 facts）
- 表情元数据：`memory/stickers.json`（emotion/note/uses/last_used）
- 日记：`diary.json`（每日自我总结）

## 面板设计

- 五大页签（看/做/看/看报告/改）：首页(状态) · 玩它(操作) · 聊天(消息) · 档案(报告) · 设置(参数)
- 每个功能项标准化交互：`toggle`(开关) / `jump`(跳转高亮) / `sheet`(详情弹层)
- 离线演示模式：`?demo=1` 使用内置示例数据，便于无设备预览
- 完整交互规格见 `docs/panel-interaction-spec.md`

## 可靠性设计（"打不死的猫"）

- 引擎级保活：宿主管家 3 秒巡更，进程级拉起
- 面板级保活：端口探活 + 进程双查
- 日志安全：**长连接写入的日志只允许就地截断，禁止 mv 轮转**（fd 跟随改名文件会撑爆磁盘）
- 内存看门狗：可用内存过低时分级处置（软清理 → 重启链路）
- 视觉切换、模型重载等重操作全部经 flag 交由宿主侧执行（沙箱内权限/路径受限）
