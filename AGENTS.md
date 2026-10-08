# AGENTS.md — 给 AI 协作者的项目速查

A股缠论分析系统。详细进度与上下文见 `docs/05-dev-progress.md`(接续工作先读)。

## 环境

- 平台: Windows + **PowerShell**(非 bash)
- Python: 3.11.9, 虚拟环境在 `.venv`(解释器 `.venv\Scripts\python.exe`)
- 密钥在 `.env`(已 gitignore, 从不提交); 模板 `config/.env.example`
- 缠论库 chan.py 以源码内置于 `vendor/chan.py`(运行时自动加 sys.path)

## 常用命令

```powershell
# 装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 本地 CLI 测试指令(开发首选, 不经飞书)
$env:PYTHONUTF8=1
.\.venv\Scripts\python.exe scripts\cli.py Q 000001

# 启动飞书机器人(WebSocket 长连接)
.\.venv\Scripts\python.exe scripts\run_bot.py

# 单元测试(111个, 不依赖网络, 改完代码先跑)
.\.venv\Scripts\python.exe -m pytest

# 手动触发定时任务 / 查任务库
.\.venv\Scripts\python.exe scripts\trigger_job.py daily --force [--send]
.\.venv\Scripts\python.exe scripts\show_tasks.py

# 分层验证脚本(访问真实网络)
.\.venv\Scripts\python.exe scripts\test_data.py          # 数据层
.\.venv\Scripts\python.exe scripts\test_llm.py           # LLM 连通性
.\.venv\Scripts\python.exe scripts\test_integration.py   # 数据->chan.py
.\.venv\Scripts\python.exe scripts\smoke_chan.py         # chan.py 冒烟
```

## 环境坑(务必注意)

1. 运行 Python 带 `PYTHONUTF8=1`; 脚本内 `sys.stdout.reconfigure(encoding="utf-8")`(控制台 GBK)。
2. 写含中文的文件(如 `.env`)用 UTF-8 **无 BOM**; PowerShell `Set-Content` 默认 GBK 会坏编码。
3. 数据层必须用 `curl_cffi`(浏览器 TLS 指纹): 本机 Clash 代理环境下 requests 直连东方财富被 RST。
4. 提交用内联身份(不改全局 git config):
   `git -c user.name="YiyuanRen" -c user.email="YiyuanRen@users.noreply.github.com" commit ...`
5. 分支: 功能开发在 `develop`。

5. PowerShell 会吞掉命令行参数里的 `@1200`(展开运算符), 带 `@` 的 S 指令请在飞书里测; 远程 ssh 命令用 `.sh` 文件 + scp, 别在双引号里嵌 `$`。
6. 飞书群聊必须从 @ 菜单选中机器人(真 mention); 同一飞书应用不要在两处同时运行(本机和 ECS 只留一个)。

## 架构(app/)

bot(飞书: 会话上下文/群@/推送) → commands(解析/路由/M) → services(analysis_service 分析+缓存, task_service B/S/C/L, notifier) → engine(chan_analyzer/multi_level/holding/report_builder) + data(fetcher/trading_calendar) + llm(factory/config_store/report_generator)。
scheduler/jobs.py: 15:05 日报 / B 盘中 / S 止损, 推送到任务所属 chat_id。models/: SQLite(tasks 按 chat_id 隔离)。
config.py 读 config.yaml+.env; logging_utils.py 统一日志。详细设计见 docs/05-dev-progress.md「关键设计」。

## 风险提示约束

所有对外报告末尾必带: `⚠️ 仅供参考，不构成投资建议`
