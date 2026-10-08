# 阶段3 开发进度 & 上下文快照

> 本文件记录**当前实际实现状态**与**本机开发环境细节**, 供新会话无缝接续。
> 最近更新: 2026-10-07

---

## 一句话现状

**环境全部就绪 + `Q` 即时查询全链路已打通并在飞书验证通过。**
下一步: 任务系统(B/S/C/L) + 调度器 + 止损监控 (Sprint 3)。

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

# 启动飞书机器人(WebSocket 长连接, 阻塞运行)
.\.venv\Scripts\python.exe scripts\run_bot.py

# 各层单测脚本
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
| 命令解析 | `app/commands/parser.py` | ✅ Q/B/S/C/L/M 六指令 |
| 命令路由 | `app/commands/router.py` | ✅ Q 全链路; B/S/C/L 占位; M 仅查看 |
| 飞书机器人 | `app/bot/feishu_bot.py` | ✅ 收消息/文本回复/Markdown 卡片/中间态提示/工作线程 |

---

## 还没做(下一步 Sprint 3 / 后续)

- [ ] **任务系统**: SQLite 模型(tasks/llm_config/analysis_cache), B/S/C/L 指令完整实现
- [ ] **调度器**: APScheduler —— 每交易日 15:05 定时分析(B+S) + S 任务盘中止损监控(9:30-15:00 每3min, 只比价) + **B 任务盘中跟踪(每30min 完整跑多级别分析, 有买点才推送, 无信号静默)**
  > 2026-10-08 用户在 GitHub 上修订了设计: B 盘中跟踪**不再**以"日线出现买点"为前置条件, 改为定时全量分析; 以 `01-product-spec.md`/`04-implementation-plan.md` 最新版为准。
- [ ] **主动推送**: 买卖点出现/触及止损 推送到飞书
- [ ] **LLM 热切换**: M 指令写 SQLite llm_config, 调用前读取(当前 M 只能查看)
- [ ] **分析缓存**: 同日已分析的 Q 读缓存(SQLite, 超2小时/收盘失效)
- [ ] **Docker 化 + 部署到阿里云 ECS**(ECS 运行时已就绪; 待写 Dockerfile/docker-compose, 并解决 ECS 上拉代码: 需在 ECS 生成 deploy key 加到 GitHub, 或用 scp/rsync)
  - 注意 chan.py 需 Python 3.11+, 镜像用 `python:3.11-slim`; 部署需带上 `.env`(不入库, 单独 scp)
- [ ] (可选)飞书卡片流式"思考过程"(04 文档任务4.4, 待用户明确)

---

## 已知问题 / 待优化

- 30分钟K线东方财富该接口只返回约1个月(~248根), 达不到方案的6个月, 但够缠论计算。
- `multi_level.py` 的入场/止损/目标为**规则化启发式**(chan.py 只给缠论元素, 不给策略价), 需实盘回测校准。
- `macd_divergence` 目前近似判断(以是否1类买卖点推断), 未来可从 chan.py bsp 特征精确取。
- 全局 CRLF 警告(cosmetic): 可加 `.gitattributes` 规范为 LF(部署到 Linux 更稳), 暂未加。
- commit 身份用内联 `-c user.name/email`(未改全局 git config); 如需固定可设本仓库 local config。

---

## 分支 & 提交

- `main`: 脚手架基线
- `develop`: 开发主线(当前所有功能在此)
- 提交署名: `YiyuanRen <YiyuanRen@users.noreply.github.com>` + Devin Co-Authored-By
