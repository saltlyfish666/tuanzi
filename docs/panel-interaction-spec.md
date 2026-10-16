# 小团子控制面板 · 交互升级规格书 v6

> 任务类型：增量开发（现有系统上做交互改造）
> 交付负责人：主理人；实现：工程师；验证：QA

## 0. 项目背景（先读这段）

"小团子"是一只跑在 **Android 平板**里的 QQ 机器人猫。系统组成：

| 文件 | 角色 | 路径 |
|---|---|---|
| `tuanzi_pkg/dashboard.html` | 控制面板（单文件内嵌 CSS/JS/字体，~137KB） | （仓库根目录） |
| `tuanzi_pkg/panel_server.py` | 面板服务端（跑在平板沙箱，端口 8898） | 同上 |
| `tuanzi_pkg/tuanzi_service.py` | 机器人引擎（聊天/表情/记忆/转达/天气/冒泡…） | 同上 |
| `tuanzi_pkg/config.json` | 引擎配置 | 同上 |

**面板结构现状**：5 个页签（`#p-home` 首页 / `#p-play` 玩它 / `#p-chat` 聊天 / `#p-brain` 档案 / `#p-settings` 设置），JS 路由函数 `go(p)`，数据来源 `GET /api/status`（每 5 秒轮询），操作接口 `POST /api/do {action}` 与 `POST /api/set {key,value}`。

**平板侧关键路径**（面板服务端运行环境，Python3）：
- 表情包图片：`BASE/pics/*.jpg`（现有 8 张：`angry_1.jpg` 等，命名 `<emotion>_<n>.jpg`）
- 表情元数据：`BASE/memory/stickers.json`（`{files:{名字:{emotion,uses,last_used,...}}}`）
- 转达任务：`BASE/relay_schedule.json`
- 人物档案：`BASE/memory/persons/*.json`（每人一份）
- BASE = `/root/tuanzi/`（沙箱内），config 同目录

## 1. 硬性技术约束（违反会造成线上事故）

1. **dashboard.html 是运行中的单文件**：vanilla ES5 风格 JS（现有代码全部用 `function(){}`、无箭头函数、无构建工具）。**保持同样风格**。
2. **不得删除现有 element id**：所有 `renderXxx` 函数依赖 id（`$("xxx")`）。重构区块时若 removals，必须同步改 JS。改完必须 `node --check` 通过。
3. **文件编码**：UTF-8、**只允许 LF 换行**（禁止 CRLF）。用 `open(p, encoding="utf-8", newline="")`。
4. **离线自包含**：不引入任何外部网络请求/CDN/新依赖。
5. **中文文案**：温暖、口语化、小白友好（例："点错了没关系，每个都会先问一遍"）。emoji 适度。
6. 面板在**平板触屏**使用：可点击元素 ≥44px 高；重要信息字号 ≥13px。
7. 修改后自检命令：
   - `python -m py_compile panel_server.py tuanzi_service.py`
   - 提取 `<script>` 内容 `node --check`（可写临时文件）
   - 检查 `open(...,'rb').read().count(b'\r\n') == 0`

## 2. 需求清单（15 项，用户原话→设计）

### 2.1 功能项交互系统（核心改造）

当前"🧭 全部功能一览"（`#featgrid`，由 `renderFeat(d.features)` 渲染）是一排静态小格。**改造为可交互的功能列表**：

后端 `panel_server.py` 的 `collect()` 里 `features` 数组每项增加字段：
```python
{"k": "🧠 本地大模型", "s": "on", "note": "Qwen2.5-VL-3B", 
 "act": "jump", "target": "settings:brain",   # 或
 "act": "sheet", "detail": "vision",          # 或
 "act": "toggle", "tkey": "night_enabled", "ton": True}  # 或 act: None
```
前端 `renderFeat` 渲染升级：
- 每项右侧显示交互提示：`toggle` → 一个开关控件（`<i class="tg on/off">`）；`jump`/`sheet` → `›`
- `toggle` 点击 → `POST /api/set {"key": tkey, "value": !current}` → 立即 `load()`
- `jump` 点击 → `go(page)` + `document.getElementById(elem).scrollIntoView({behavior:"smooth",block:"center"})` + 目标节点加 `.flash` 类（CSS 高亮脉冲动画，1.5 秒后移除）
- `sheet` 点击 → 打开详情弹层（见 2.2）

### 2.2 详情弹层系统（复用现有 sheet）

现有 `#sheet`/`#shSub`/`#shBody` 弹层组件（看 `openSheet`/`autoSheet` 用法）。新增 **`openFeatSheet(k)`**：`fetch("/api/detail?k="+k)` → 渲染标题+正文+可选按钮。

**panel_server 新增 `GET /api/detail?k=<key>`**，返回：
| k | 内容 |
|---|---|
| `vision` | `{model:"Qwen2.5-VL-3B", vision:true, note:"约40秒看一张图"}` ← 读 brain 检测逻辑复用 |
| `chat` | `{c2c_enabled, group_enabled, replies_today, persons}`（私聊+群聊**合并为一卡**：两个开关 + 今日回复数） |
| `memory` | 扫描 `memory/persons/*.json` → `{persons:[{name, uid, intimacy, last, facts:[前5条], rounds}]}`（读不到的文件字段留空，勿崩） |
| `relay` | 读 `relay_schedule.json` → `{tasks:[{when, to, text, done}]}`；空则 `[]` |
| `qzone` | `{enabled, last_check, watched:[...]}`（有多少给多少，读 status/config，读不到给静态说明） |
| `sticker` | `{count, images:["/stk/happy_1.jpg", ...]}`（前 30 张） |
| `greet` | `{enabled, morning_hour, evening_hour}`（config 读） |
| `weather` | 不需要（跳转） |
| `night` | `{enabled, start:0, end:8}` |
| `poke` | `{enabled}` |

**面板新增静态路由** `GET /stk/<filename>`：从 `BASE/pics/` 读取图片返回（防路径穿越：只允许 `[A-Za-z0-9_.-]+\.(jpg|png|gif)`，用 `os.path.basename` + 白名单校验）。

### 2.3 十五项逐个映射

| # | 功能项 | act | 目标/详情 | 备注 |
|---|---|---|---|---|
| 1 | 🧠 本地大模型 | jump | `settings:brainGrp`（设置页大脑卡，给它 id）| 跳到并高亮 |
| 2 | 👁 看图说话 | sheet | `vision` | 详情显示当前模型名 |
| 3 | 💬 聊天互动（**私聊+群聊合并成一项**） | sheet | `chat` | 弹层内两个开关 |
| 4 | 🗣 语音消息 | jump | `settings:voiceRow`（习惯组，给它锚点 id）| |
| 5 | 😸 表情包 | sheet | `sticker` | 图墙预览 + 数量（**并扩到 30 张**见 §3）|
| 6 | 🐟 投喂小鱼干 | jump | `play:btnFeed` | 玩它页喂食按钮，高亮 |
| 7 | 💾 分人记忆 | sheet | `memory` | 人员列表 → 点击某人再进一层小弹层看 facts |
| 8 | ⏰ 定时转达 | sheet | `relay` | 列出任务；无任务显示"还没有安排" |
| 9 | 🌟 QQ空间关注 | sheet | `qzone` | 状态详情 |
| 10 | 🌤 天气感知 | jump | `settings:cityGrp` | 跳位置设置 |
| 11 | 🎂 节日&早晚安 | sheet | `greet` | 弹层内两个时间选择（早/晚，`<select>` 0-23 时）+ 开关 |
| 12 | 🌙 深夜免打扰 | toggle+sheet | `night` | 开关 + 弹层显示时间段 |
| 13 | 👉 戳一戳回应 | toggle | — | 新配置键 |
| 14 | 🫧 主动找你聊天 | toggle | 用现有 `toggle_bubbles` action（不走 /api/set）| 状态读 `bubbles_paused` |
| 15 | 🔁 断线自动补偿 / 🧹 自愈 / 📔 日记 | 纯信息，act=None | | |

### 2.4 引擎侧（tuanzi_service.py + config.json 默认值）

新增配置键 + 引擎读取（找到对应逻辑点，尊重默认值保持现行为）：
- `night_enabled`(默认 true)：深夜免打扰时段判断前先查开关
- `poke_enabled`(默认 true)：`handle_poke` 开头判断
- `weather_enabled`(默认 true)：天气获取 worker 尊重开关（关闭时不再请求）
- `greet_enabled`(默认 true)：节日/早晚安事件 worker 总开关
- `greet_morning_hour`(默认 8)、`greet_evening_hour`(默认 22)：替换现在硬编码的 `if h == 8` / `elif h == 22`
- 已存在：`c2c_enabled`、`group_enabled`（无需改，只在 UI 暴露）

### 2.5 档案页"部件"简化

现在 `renderComps` 渲染 9 张卡（`#comps`）。**改为**：
- 全部正常 → 单行卡："🛡 系统自检：一切正常，管家在守护 ✓"（一行，绿色点）
- 有异常 → 只列出异常的部件卡（保留原详情的修复步骤能力，点击弹层看 fix_steps）
- 标题改为"🛡 系统自检"；正常时整个区块高度 ≤ 70px

### 2.6 设置页紧凑化

用户反馈"占太大。就四个按钮占一整页没必要"。改造：
- `details.grp` 全部默认**收起**（去掉 `open` 属性）
- `.setrow` 行更紧凑：现在滑块独占一行 → 改为两列布局或减小 padding（滑块行 min-height 从 52 降到 44，`gbody` padding 减小）
- 习惯组内的"保存"按钮保持（不能自动保存，引擎是轮询 flag 机制）
- 云大脑组内表单同理紧凑

### 2.7 附加（用户要求"网上找找还能加什么"）

- 聊天互动弹层底部加按钮"**去 QQ 里找它玩 →**"：`<a href="mqq://im/chat?chat_type=wpa&uin=10003&version=1&src_type=web">`，新窗口打开失败则 toast 提示（QQ 号 10003 是猫咪账号，硬编码即可）
- 首页 hero 下加一个小的"今天它乖吗？"快捷状态条（可选，若时间不够可跳过）

## 3. 表情包扩充到 30 张

- 当前：`pics/` 下 8 张 `<emotion>_1.jpg`
- **先查引擎的表情域**：`tuanzi_service.py` 里 `STICKER_CAPTIONS` 的 key 与 mood→emotion 的映射逻辑，确定合法 emotion 集合
- 生成 **22+ 张新图**（JPG，512×512）放入 `napcat_deploy/sticker_pack/`（本机新目录），命名 `<emotion>_<n>.jpg`（n 从 2 起）
- 风格：**扁平可爱猫咪表情包**——圆脸大眼猫 + 中文短句（如"哈哈哈哈""困死了喵""饿饿""抱抱""气鼓鼓"）。中文字体用 `C:\Windows\Fonts\msyh.ttc`（微软雅黑）
- 保证每个 emotion ≥2 张、总计 ≥30 张；元数据（`stickers.json`）**不用**手工写——引擎发现新文件会自动补元数据（先读代码确认 `sticker_files()` 逻辑，若不自洽则说明）
- 交付物：图片 + `sticker_pack/MANIFEST.txt`（文件名清单）

## 4. 验收标准（QA 将逐条核对）

1. `python -m py_compile` 三文件全过；`node --check` 面板 JS 过；无 CRLF
2. `/api/detail?k=` 每个 key 都返回 200 + 合法 JSON（可在本机以 mock BASE 目录跑单测：MONKEY-PATCH BASE 路径或用临时目录）
3. 面板在离线 mock 模式（`?demo=1`）下五个页面可切换、无 console 报错
4. 功能项交互：toggle 可切换、jump 有高亮、sheet 能打开（用 mock 数据截图验证）
5. `sticker_pack/` ≥22 张新图，合法 JPG，文件名符合规范
6. 设置页默认收起、更紧凑
7. 现有 id 无一缺失（列出 diff 说明删改）

## 5. 交付格式

- 改动文件：直接修改 `napcat_deploy/tuanzi_pkg/` 内三个文件（就地改）
- 新增：`sticker_pack/`、本规格书的实现说明回复（改了什么、验证结果、遗留问题）
- **不要部署到平板**（部署由主理人统一执行；本地验证即可）
