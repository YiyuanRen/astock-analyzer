# astock-analyzer · A股缠论分析系统

通过飞书机器人,用户发送股票编码指令,系统基于**缠论(缠中说禅理论)**对 A 股做**多级别技术分析**,辅助判断**买入价/卖出价**,并支持任务式跟踪 + 买卖点出现时主动推送。

> ⚠️ 本项目所有分析结果仅供参考,不构成投资建议。

---

## 核心能力

- **6 条飞书指令**: `Q`即时查询 / `B`买入跟踪 / `S`卖出跟踪 / `C`取消 / `L`列表 / `M`切换LLM
- **多级别联立(区间套)**: 日线定方向 + 30分/5分定精确入场价
- **任务式跟踪**: B/S 任务 + 收盘定时分析 + 盘中止损监控 + 买卖点主动推送
- **LLM 报告生成**: 结构化缠论结果 → 自然语言报告(LLM 不参与缠论计算本身)
- **LLM 热切换**: 飞书 `M` 指令运行时切换模型(当前启用 MiMo / DeepSeek)

## 技术栈

| 用途 | 方案 |
|---|---|
| 飞书接入 | lark-oapi (WebSocket, 无需公网IP) |
| 行情数据 | AKShare (免费) |
| 缠论计算 | [Vespa314/chan.py](https://github.com/Vespa314/chan.py) (vendored, 见 `vendor/`) |
| LLM | OpenAI 兼容协议, 工厂模式适配, SQLite 热切换 |
| 调度 | APScheduler + SQLite |
| 部署 | Docker (阶段3后期) |

## 项目结构

```
astock-analyzer/
├── app/
│   ├── bot/          # 飞书机器人接入 (lark-oapi WebSocket)
│   ├── commands/     # Q/B/S/C/L/M 指令解析与处理
│   ├── engine/       # 缠论分析引擎 (chan.py 封装 + 多级别联立 + 报告组装)
│   ├── data/         # AKShare 数据拉取层
│   ├── llm/          # LLM 通用适配层 (base/factory/config_store/providers)
│   ├── scheduler/    # APScheduler 调度 (定时 + 事件触发)
│   └── models/       # SQLite 数据模型
├── config/           # config.yaml + .env.example
├── vendor/chan.py/   # 内置缠论库源码
├── scripts/          # 验证/工具脚本
├── tests/
├── docker/
└── docs/             # 产品/技术/实施方案文档
```

## 开发环境 (本机, Windows)

```powershell
# 1. 创建虚拟环境 (Python 3.11+)
python -m venv .venv

# 2. 安装依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. 配置密钥
Copy-Item config\.env.example .env
# 编辑 .env 填入 FEISHU / LLM API Key

# 4. 验证缠论引擎
.\.venv\Scripts\python.exe scripts\smoke_chan.py
```

## 文档

详见 [`docs/`](docs/):

- [HANDOVER.md](docs/HANDOVER.md) — 跨会话交接入口
- [01-product-spec.md](docs/01-product-spec.md) — 产品方案
- [02-tech-design.md](docs/02-tech-design.md) — 技术方案
- [03-infra-setup.md](docs/03-infra-setup.md) — 基础设施申请指引
- [04-implementation-plan.md](docs/04-implementation-plan.md) — Sprint 实施计划
