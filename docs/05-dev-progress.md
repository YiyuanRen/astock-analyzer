# 阶段3 开发进度 & 上下文快照

> 本文件记录**当前实际实现状态**与**本机开发环境细节**, 供新会话无缝接续。
> 最近更新: 2026-10-08

---

## 一句话现状

**Q/B/S/C/L/M 六条指令全部实现, 支持飞书私聊 + 群聊, 定时任务(15:05日报/B盘中跟踪/S止损监控)与主动推送已实现; 本机真机联调通过, 111 个单测全绿。**
**下一步: 部署到阿里云 ECS(运行时已装好, 尚未部署任何代码)。**

### 关键设计(已落地, 勿随意推翻)
- **会话模型**: 以飞书 `chat_id` 统一"会话"。任务按 `chat_id` 归属(私聊任务属于该私聊, 群任务**群共享**); 在哪个会话提交指令, 回复与主动推送就发回哪个会话。群里只响应 **@机器人** 的消息(启动时取机器人 open_id 判定), 剥离 `@_user_N` 占位符。推送**不 @ 人**。
- **S 止损**: 结构止损(日线最近支撑, 须低于现价≥2%, 再下浮1.5%) + 成本保护(成本×0.92, 取较高者); 每日 15:05 重算且**只上移不下移**; 重发 `S 代码` 重置。参数在 `config.yaml: task`。
- **止损提醒节奏**: 当日首次触及立即推; 仍低于则每30分钟重复; 回升重置; 次日重新首推。
- **B 盘中**: 时点对齐30分钟K线收盘+1分钟(10:01,10:31,11:01,11:31,13:31,14:01,14:31); 日线买点才推, 信号键=`类型|所在笔|是否30m共振`去重(共振确认时升级再推一次); 15:05 日报刷新信号键。盘中推送带"盘中信号, 以日线收盘确认为准"。
- **日报**: B、S 每天都推完整卡片; S 卡片头部(成本/市值/浮盈/止损/目标)由代码确定性生成, 不经 LLM。
- **互斥**: 同会话同股票最多1个活跃任务; `S` 自动取消 B(历史保留); 已有 S 时发 `B` 被拒; 重发 `B` 提示已存在。
- **M**: 切换前做1次探活; **管理员白名单** `.env: ADMIN_OPEN_IDS`(逗号分隔; 空=不限制); 无权限者被拒并显示其 open_id。
- **交易日历**: 上证指数日K日期集合(数据驱动, 自动跳过节假日, 已实测国庆休市), 失败退化为工作日。
- **Q 缓存**: 收盘/休市同 session 一直有效, 盘中<2h 有效, 跨收盘失效; 定时任务 `force` 重算并写回缓存。
- **调度器**: 内存 jobstore(不引入 SQLAlchemy); 重启后 cron 自动重建, 持久状态全在 `tasks` 表。

---

## 基础设施状态

| 项目 | 状态 | 凭证/位置 |
|---|---|---|
| 本机 Python | ✅ 3.11.9 (winget 安装) | `%LOCALAPPDATA%\Programs\Python\Python311` |
| 虚拟环境 | ✅ | `e:\Project\astock-analyzer\.venv` |
| GitHub 仓库 | ✅ | `git@github.com:YiyuanRen/astock-analyzer.git` (main+develop) |
| SSH key | ✅ 已配置并认证 | `~/.ssh/id_ed25519` (无密码) + `~/.ssh/config`(KEX 修复) |
| 飞书自建应用 | ✅ 已接入验证 | App ID/Secret 在 `.env` |
| LLM: DeepSeek | ✅ 默认, 已充值 | `DEEPSEEK_API_KEY` in `.env` |
| LLM: MiMo | ✅ 备用(可 M 切换) | `MIMO_API_KEY` (tp- Token Plan) in `.env` |
| 阿里云 ECS | ✅ 已购买, 运行时已装好(2026-10-08), **尚未部署任何代码** | `8.153.91.185`, Ubuntu 22.04.5, 2C/3.6G+4G swap, root; 私钥 `secrets/ecs_access_key.pem`(gitignore), `.env` 的 `ECS_HOST/ECS_USER/ECS_SSH_KEY`; 本机 SSH 别名 `ssh astock-ecs`(在 `~/.ssh/config`) |

### ECS 已装运行时(由 `scripts/ecs_setup.sh` 幂等安装, 可重跑)

Docker 29.8.2 + Compose v5.6.0(开机自启, 日志轮转 10m×3) / Python 3.11.17 / git / build-essential / tmux / pip 阿里云镜像 / 时区 Asia/Shanghai。
已实测: 容器可跑, `python:3.11-slim` 已拉取, venv+pip 安装正常。

ECS 踩坑:
1. **阿里云 docker-ce apt 镜像偶发"同步中"**(文件大小校验失败)→ 脚本已加重试, 回落官方源, 再回落 Ubuntu 自带 `docker.io`。
2. **Docker Hub 在 ECS 直连不通** → `/etc/docker/daemon.json` 配了 `docker.m.daocloud.io`、`docker.1ms.run` 镜像加速(第三方公共源, 失效时需更换)。
3. 远程命令别用 PowerShell 双引号内嵌 `$`/嵌套引号 → 一律写成 `.sh` 文件 `scp` 上传再 `ssh "bash /tmp/x.sh"`;
   长任务用 `nohup ... &` 在服务器后台跑, 避免本地中断杀掉 apt(曾因此留下孤儿 apt 占 dpkg 锁)。
4. 安全组目前只需放行 22; 机器人走飞书 WebSocket 出站长连接, 无需开放入站端口。

> `.env` 已 gitignore, **从不提交**。模板见 `config/.env.example`。

---

## 本机环境关键注意事项(踩过的坑)

1. **Shell 是 PowerShell**(非 bash)。写 `.env` 等含中文文件要用 UTF-8 无 BOM
   (`[System.IO.File]::WriteAllText(path, text, UTF8Encoding($false))`), 否则 GBK 编码导致 dotenv 解析失败。
2. **控制台 GBK**: 运行 Python 脚本要带 `PYTHONUTF8=1`, 脚本里也 `sys.stdout.reconfigure(encoding="utf-8")`。
3. **SSH KEX bug**: Windows OpenSSH 9.5(LibreSSL)会广告它不支持的 `sntrup761x25519-sha512`,
   已在 `~/.ssh/config` 为 github.com 指定可用 KexAlgorithms 修复。
4. **系统代理 Clash (127.0.0.1:7890)**: 东方财富数据直连会被 **TLS 指纹拦截**(requests/urllib3 被 RST)。
   解决: 数据层改用 **curl_cffi (impersonate=chrome)**, 浏览器 TLS 指纹直连, 并带重试。
5. **MiMo endpoint**: 原方案文档写的 `api.mimo.ai/v1` 是错的。`tp-` key 属 Token Plan,
   正确地址 `https://token-plan-cn.xiaomimimo.com/v1`, 模型 `mimo-v2.6-flash/pro`。
6. **MiMo flash 偏慢**(thinking 开销), 单次 Q 可能 >15s; 故默认改用 DeepSeek 且超时设 25s。

---

## 如何运行(本机)

```powershell
# 本地 CLI 测试(不经飞书) —— 开发期首选
$env:PYTHONUTF8=1
.\.venv\Scripts\python.exe scripts\cli.py Q 000001
.\.venv\Scripts\python.exe scripts\cli.py          # 交互式

# 启动飞书机器人 + 定时任务调度器(阻塞运行)
.\.venv\Scripts\python.exe scripts\run_bot.py

# 单元测试(111个, 不依赖网络)
.\.venv\Scripts\python.exe -m pytest

# 手动触发定时任务(不必等盘中): dry-run 只打印; --send 真实推送到各任务所属会话
.\.venv\Scripts\python.exe scripts\trigger_job.py daily --force --send
.\.venv\Scripts\python.exe scripts\trigger_job.py intraday --force
.\.venv\Scripts\python.exe scripts\trigger_job.py stoploss --force --price 600519=1100
# 查看数据库任务(PowerShell 里 `@1200` 会被吃掉, 带 @ 的指令请在飞书里测或加引号)
.\.venv\Scripts\python.exe scripts\show_tasks.py [--all]
# CLI 模拟会话: $env:CLI_CHAT_ID="x"; $env:CLI_GROUP=1; $env:CLI_OPEN_ID="ou_x"

# 各层集成脚本(访问真实网络)
.\.venv\Scripts\python.exe scripts\smoke_chan.py        # chan.py 合成数据冒烟
.\.venv\Scripts\python.exe scripts\test_data.py         # 数据层三周期+实时价
.\.venv\Scripts\python.exe scripts\test_llm.py          # DeepSeek/MiMo 连通性
.\.venv\Scripts\python.exe scripts\test_integration.py  # 真实数据->chan.py
```

---

## 已实现模块

| 模块 | 文件 | 状态 |
|---|---|---|
| 配置加载 | `app/config.py` | ✅ config.yaml + .env |
| 日志 | `app/logging_utils.py` | ✅ |
| 数据层 | `app/data/fetcher.py` | ✅ 东方财富+curl_cffi: fetch_daily/30min/5min/quote/realtime_price/fetch_all_levels(并发+重试) |
| 缠论引擎(单级别) | `app/engine/chan_analyzer.py` | ✅ chan.py 封装 → 笔/线段/中枢/买卖点/背驰/锚点 |
| 多级别联立 | `app/engine/multi_level.py` | ✅ 区间套 + 操作建议(方向/入场/止损/目标/赢赔比/仓位/支撑压力) |
| 报告组装 | `app/engine/report_builder.py` | ✅ 标准字段 + 成交量状态 + 纯文本降级渲染 |
| LLM 报告 | `app/llm/report_generator.py` | ✅ 结构化→自然语言, 25s 超时降级, 卡片友好 Markdown |
| LLM 适配 | `app/llm/base.py` `factory.py` `providers/openai_compatible.py` | ✅ OpenAI 兼容(DeepSeek/MiMo) |
| 命令解析 | `app/commands/parser.py` | ✅ Q/B/S/C/L/M 六指令(M 的 model 可省略) |
| 命令路由 | `app/commands/router.py` | ✅ 六指令全部接通, ctx 带 chat_id/sender_open_id |
| M 指令/权限 | `app/commands/model_cmd.py` `permissions.py` | ✅ 查看/热切换/探活/管理员白名单 |
| 飞书机器人 | `app/bot/feishu_bot.py` `message_utils.py` | ✅ 会话上下文/群@判定/回复/卡片/按 chat_id 主动推送(含重试) |
| 存储层 | `app/models/database.py` `task_repo.py` `cache_repo.py` | ✅ SQLite(WAL, 每次新连接), tasks 部分唯一索引(chat_id,stock_code) |
| LLM 热配置 | `app/llm/config_store.py` | ✅ 每次调用前读库, 无记录回落 config.yaml |
| 交易日历 | `app/data/trading_calendar.py` | ✅ 数据驱动交易日 + 交易时段 + session_key |
| 分析服务 | `app/services/analysis_service.py` | ✅ 完整流水线 + 缓存(Q/B/S/调度共用) |
| 持仓逻辑 | `app/engine/holding.py` | ✅ 结构止损/成本保护/目标/ratchet/盈亏/头部 |
| 任务服务 | `app/services/task_service.py` | ✅ B/S/C/L(按会话隔离, 互斥规则) |
| 通知器 | `app/services/notifier.py` | ✅ Console/Recording; FeishuBot 即通知器 |
| 调度/任务 | `app/scheduler/jobs.py` | ✅ 日报/B盘中/S止损 + build_scheduler |

---

## 还没做(下一步)

- [ ] **Docker 化 + 部署到阿里云 ECS**(ECS 运行时已就绪; 待写 Dockerfile/docker-compose(`restart: always`, `./data` 卷), 并解决 ECS 上拉代码: 在 ECS 生成 deploy key 加到 GitHub, 或用 scp/rsync)
  - chan.py 需 Python 3.11+, 镜像用 `python:3.11-slim`; 部署需带上 `.env`(不入库, 单独 scp); `data/` 卷持久化 SQLite
  - 部署后在 ECS 上同样做私聊 + 群各测一遍, 并**停掉本机机器人**(同一飞书应用不要两处同时跑, 否则消息会被两边抢/重复处理)
  - 线上验证需等一个真实交易日观察 15:05 日报、盘中 B 跟踪、止损监控的自动触发(本机仅用 trigger_job 手动触发验证过)
- [ ] 把自己的 open_id 配进 `.env` 的 `ADMIN_OPEN_IDS`(联调时用假 ID 验证了拦截, `.env` 当前为空=不限制); open_id 见机器人日志 `sender=`
- [ ] (可选)飞书卡片流式"思考过程"(04 文档任务4.4, 待用户明确)
- [ ] (可选)推送失败(机器人被移出群/群解散)时的任务自动挂起/告警; 目前仅记日志并保留任务

---

## 已知问题 / 待优化

- 30分钟K线东方财富该接口只返回约1个月(~248根), 达不到方案的6个月, 但够缠论计算。
- `multi_level.py` 的入场/止损/目标为**规则化启发式**(chan.py 只给缠论元素, 不给策略价), 需实盘回测校准。
- `macd_divergence` 目前近似判断(以是否1类买卖点推断), 未来可从 chan.py bsp 特征精确取。
- 盘中日线最后一根未收盘, chan.py 的买卖点可能消失(已用"盘中信号"标注+去重缓解, 以15:05为准)。
- 本机开发库 `data/astock.db` 里留有联调任务(私聊: B 000001 / S 600519 @1200×100; 群: B 000001), 想清理在飞书发 `C 代码`。
- `trigger_job.py` 退出时会打印一条 lark SDK 的 `Task was destroyed but it is pending` 错误日志, 无害(SDK 后台缓存清理任务)。
- 群聊必须从飞书 @ 菜单选中机器人(真 @); 手敲文本 `@分析助手` 不是 mention, 会被忽略。
- CRLF 警告(cosmetic): 已为 `.sh`/`Dockerfile` 加 `.gitattributes` 强制 LF, 其余告警可忽略。
- commit 身份用内联 `-c user.name/email`(未改全局 git config); 如需固定可设本仓库 local config。

---

## 分支 & 提交

- `main`: 脚手架基线
- `develop`: 开发主线(当前所有功能在此)
- 提交署名: `YiyuanRen <YiyuanRen@users.noreply.github.com>` + Devin Co-Authored-By
