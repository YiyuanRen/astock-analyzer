# Changelog / 变更记录

> 记录每次阶段2方案的调整，便于跨会话追溯决策。

---

## 2026-10-07 — 阶段2方案补充确认

### 新增/变更

1. **飞书卡片思考过程** — 待定（可选功能）
   - 分析耗时 ≤10s：不展示思考过程，直接返回完整报告
   - 分析耗时 >10s：启用飞书卡片流式更新，展示思考步骤（图1展开/图2折叠效果）
   - 飞书 patch 接口频率限制 ≤5次/秒，内容节流，卡片大小上限 ~30KB

2. **飞书能力费用确认** — 全部免费
   - 自建应用、机器人能力、消息收发、卡片消息 patch 更新、WebSocket 长连接均免费

3. **模型池新增**
   - Kimi（月之暗面）：api.moonshot.cn/v1
   - GLM（智谱）：open.bigmodel.cn/api/paas/v4
   - MiMo（小米）：endpoint待正式开放

4. **模型热切换** — 飞书 M 指令，运行时切换，无需重启

5. **基础设施全部未申请** — 阶段3第一步引导用户逐项申请
   - 阿里云 ECS ecs.e-c1m2.large（2C4G，上海）
   - 飞书自建应用
   - LLM API Key（至少一家）
   - Git 远程仓库

6. **云服务器型号锁定** — `ecs.e-c1m2.large`

---

## 2026-10-07 — 阶段3 启动: 环境搭建 + Q 全链路打通

### 基础设施
- 本机 Python 3.11.9 + venv; 依赖安装(akshare/lark-oapi/openai/apscheduler/curl_cffi 等)
- GitHub 仓库 `YiyuanRen/astock-analyzer`(main+develop); 生成 SSH key 并修复 Windows OpenSSH KEX bug(`~/.ssh/config`)
- 飞书自建应用接入(WebSocket 验证通过); DeepSeek+MiMo 两家 LLM 均验证可用
- 方向调整: **先本机跑通**, 阿里云 ECS 部署推迟

### 技术决策修正
- **数据源**: 东方财富公开API + curl_cffi(浏览器TLS指纹), 规避本机 Clash 代理环境对 requests 的 TLS 指纹拦截
- **MiMo endpoint 修正**: `https://token-plan-cn.xiaomimimo.com/v1`(原 `api.mimo.ai/v1` 作废), 模型 `mimo-v2.6-flash/pro`
- **默认模型**: deepseek-chat(更快); LLM 超时 15s→25s
- **chan.py**: 以源码 vendoring 内置(commit 429d6ed)

### 功能实现(develop 分支)
- 脚手架 + 配置加载 + 统一日志
- 数据层: fetch_daily/30min/5min/quote/realtime_price + 并发+重试
- 缠论引擎: 单级别结构化分析 + 多级别区间套 + 报告组装(含纯文本降级)
- LLM 适配层(OpenAI 兼容) + 报告生成(超时降级)
- 命令解析(Q/B/S/C/L/M) + 路由(Q 全链路, 余占位)
- 飞书机器人: 收消息/文本回复/Markdown 卡片/中间态提示/工作线程
- **`Q` 即时查询端到端验证通过**(CLI + 飞书)

### 下一步
- Sprint 3: 任务系统(B/S/C/L + SQLite) + 调度器 + 盘中止损监控 + 主动推送 + LLM 热切换
- 详见 `05-dev-progress.md`

---

## 2026-10-09 — 部署上线 ECS(Docker)

- **部署方式**: GitHub deploy key(只读) → ECS `git pull` → 服务器上 `docker compose build` → 容器运行(`restart: always`, `./data` 卷, `.env` 运行时注入, TZ=Asia/Shanghai, 日志轮转)。依赖版本由 `constraints.txt` 固定为本机已验证版本。
- **验证**: 容器内全链路(ECS→东方财富/DeepSeek, Q≈6s)、飞书私聊+群、`kill -9` 模拟崩溃自动拉起并重连、重启后任务不丢。
- **踩坑**: `docker kill` 不会触发 `restart: always`(视为手动停止)。
- **运维**: `scripts/ecs_deploy.sh` 一键发布; 本机机器人停用, 线上为唯一实例。

---

## 2026-10-08 — 阶段3 功能全部实现(本机)

- **新增需求: 飞书群聊支持** — 以 `chat_id` 统一会话; 群里仅响应 @机器人, 任务群共享, 在哪提交就推回哪(私聊→私聊, 群→群), 推送不 @ 人。需开通权限 `im:message.group_at_msg:readonly`。
- **B/S/C/L 指令**: 互斥规则(S 自动取消 B、已有 S 拒绝 B、重发 S 重置止损)。
- **S 止损规则**: 结构止损 + 成本保护取高; 每日收盘重算且只上移不下移; 触及后首次立即推 + 每30分钟重复, 回升重置。
- **定时任务**: 15:05 日报(B/S 每天完整卡片) / B 盘中跟踪(时点对齐30分钟K线收盘+1分钟, 信号键去重, 共振确认升级再推) / S 止损监控(每3分钟比价)。
- **M**: SQLite 热切换 + 探活 + 管理员白名单(`ADMIN_OPEN_IDS`)。
- **基础**: SQLite 存储、数据驱动交易日历(上证指数日K, 自动识别节假日)、分析缓存、pytest(111 个测试)。
- **ECS**: 已购买, Docker/Python3.11 等运行时已装(`scripts/ecs_setup.sh`), 待部署代码。

---

## 2026-10-08 — 价格与调度设计修正

1. **ECS 价格修正** — ecs.e-c1m2.large 包年标准价约 ¥800-1200/年，不是 ¥199/年（后者是特定活动机型，非本型号）。带宽「按使用流量 5Mbps」评估：完全满足需求，峰值约 2-4Mbps，按量流量费每月 < ¥1。

2. **B任务盘中逻辑修正** — 每 30 分钟定时拉取所有 B 任务股票的 3 周期 K 线并跑完整多级别缠论分析，有买点则推送，无信号则静默。不是"日线出现买点后才触发低级别跟踪"。
