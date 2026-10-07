# 阶段2：技术方案设计

> 状态：✅ 已对齐。本文件为冻结的技术方案。

---

## 1. 系统架构

```
飞书用户
   │ 发/收消息
飞书机器人（lark-oapi WebSocket，无需公网IP）
   │
命令解析层（Q/B/S/C/L/M → 路由到对应 Handler）
   │
   ├── 即时查询(Q)
   ├── 任务管理器(B/S/C/L)
   └── 调度引擎(APScheduler)
          │
   ┌──────┴──────┐
   │  分析引擎    │
   │  ① AKShare → K线数据（日线+30分+5分）
   │  ② chan.py  → 多级别缠论计算（笔/线段/中枢/买卖点/MACD背驰）
   │  ③ LLM      → 结构化结果 → 自然语言报告
   └──────┬──────┘
          │
   存储层（SQLite）：任务状态/持仓/分析缓存/当前LLM配置
```

---

## 2. 技术选型表（冻结）

| 层次 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.11+ | chan.py/AKShare/lark-oapi 均为 Python |
| 版本控制 | Git + GitHub/Gitee | main/develop 双分支 |
| 飞书接入 | lark-oapi (WebSocket) | 官方SDK，无需公网IP |
| K线数据 | AKShare | 免费，无token门槛 |
| 缠论引擎 | Vespa314/chan.py | 多级别联立+区间套+MACD动力学 |
| LLM（默认） | GPT-4o-mini | 可运行时热切换 |
| LLM（国内备选） | DeepSeek-V3 | 成本最低，中文最优 |
| LLM适配层 | 自研 factory 模式 | 支持9家provider |
| 调度 | APScheduler | cron+interval，SQLite jobstore |
| 存储 | SQLite | 单机无依赖 |
| 容器化 | Docker + docker-compose | restart:always保活 |
| 云服务器 | 阿里云 ECS ecs.e-c1m2.large | 2C4G，上海，¥199/年 |
| 监控告警 | 飞书 webhook 自推送 | 异常推到私聊 |

---

## 3. 项目仓库结构

```
astock-analyzer/
├── app/
│   ├── bot/          # 飞书机器人接入层（lark-oapi WebSocket）
│   ├── commands/     # Q/B/S/C/L/M 指令处理
│   ├── engine/       # 缠论分析引擎（chan.py封装 + 多级别联立）
│   ├── data/         # AKShare 数据拉取层
│   ├── llm/          # LLM 通用适配层
│   │   ├── base.py           # BaseLLMClient 抽象接口
│   │   ├── factory.py        # get_llm_client 工厂 + PROVIDER_BASE_URLS
│   │   ├── config_store.py   # SQLite 读写当前模型配置（热切换）
│   │   └── providers/
│   │       ├── anthropic_client.py      # 独立 SDK
│   │       └── openai_compatible.py     # 其余8家共用
│   ├── scheduler/    # APScheduler 调度（定时 + 事件触发）
│   └── models/       # SQLite 数据模型
├── config/
│   ├── config.yaml   # 主配置
│   └── .env.example  # 环境变量模板（不提交真实密钥）
├── tests/
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── .github/workflows/  # CI/CD（可选）
├── .gitignore          # 排除 .env、__pycache__、*.db
└── requirements.txt
```

---

## 4. LLM 通用适配层

### 设计模式
工厂模式 + 抽象接口。Anthropic 走独立 SDK，其余 8 家统一走 OpenAI 兼容协议。

```python
# app/llm/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class LLMResponse:
    content: str
    model: str
    input_tokens: int
    output_tokens: int

class BaseLLMClient(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], **kwargs) -> LLMResponse: ...

    @abstractmethod
    async def stream_chat(self, messages: list[dict], **kwargs):  # 流式，用于飞书卡片实时更新
        ...
```

```python
# app/llm/factory.py
PROVIDER_BASE_URLS = {
    "openai":    None,
    "anthropic": None,   # 走独立 SDK，不用此表
    "grok":      "https://api.x.ai/v1",
    "gemini":    "https://generativelanguage.googleapis.com/v1beta/openai/",
    "qwen":      "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek":  "https://api.deepseek.com",
    "kimi":      "https://api.moonshot.cn/v1",
    "glm":       "https://open.bigmodel.cn/api/paas/v4",
    "mimo":      "https://api.mimo.ai/v1",   # 待小米MiMo正式开放，确认endpoint
}

def get_llm_client(config: dict):
    provider, model, api_key = config["provider"], config["model"], config["api_key"]
    if provider == "anthropic":
        from app.llm.providers.anthropic_client import AnthropicClient
        return AnthropicClient(api_key=api_key, model=model)
    from app.llm.providers.openai_compatible import OpenAICompatibleClient
    return OpenAICompatibleClient(api_key=api_key, model=model,
                                  base_url=PROVIDER_BASE_URLS.get(provider))
```

### 模型池（9家，全部 OpenAI 兼容除 Anthropic）
| Provider | base_url | 模型名示例 | 状态 |
|---|---|---|---|
| openai | 官方默认 | gpt-4o-mini | ✅ |
| anthropic | 独立SDK | claude-haiku-4-5-20251001 | ✅ |
| grok | api.x.ai/v1 | grok-3-mini | ✅ |
| gemini | generativelanguage.../openai/ | gemini-2.5-flash | ✅ |
| qwen | dashscope.../compatible-mode/v1 | qwen-plus | ✅ |
| deepseek | api.deepseek.com | deepseek-chat | ✅ |
| kimi | api.moonshot.cn/v1 | moonshot-v1-8k | ✅ |
| glm | open.bigmodel.cn/api/paas/v4 | glm-4-flash | ✅ |
| mimo | 待确认 | — | ⏳ 待小米开放 |

### 热切换机制
- 当前模型配置**存 SQLite**，每次 LLM 调用前读取（不是启动时加载一次）
- 飞书 `M` 指令运行时切换，无需重启/重新部署
- `M` 查看当前；`M qwen qwen-plus` 切换
- **API Key 不通过飞书传入**（安全），仍从 `.env` 加载对应 provider 的 key；切到未配置 key 的 provider 返回错误

---

## 5. 飞书卡片流式更新（展示 LLM 思考过程）

- 飞书卡片支持 **patch 接口原地更新**（修改已发出的卡片，非发新消息）
- 流程：发"分析中"卡片拿 message_id → LLM stream=True 流式输出 → 每 chunk 节流后 patch 更新 → 完成后替换为完整报告
- **限制：** patch 频率 ≤5次/秒，必须节流（每500ms或每3chunk更新一次）；卡片有大小限制，思考过程截断展示
- **需权限：** `im:message:send_as_bot` + `im:message:update_as_bot`
- 卡片结构：header + 思考过程（折叠accordion，默认收起）+ 主体报告

---

## 6. 预期执行耗时分析

### Q 命令 / B·S 单股分析
```
AKShare 拉取3周期（日线3年+30分6月+5分1月）： 3-6s（可并发）
chan.py 多级别计算（区间套）：                 0.5-2s（CPU密集）
LLM 生成报告：                                3-6s（API）
─────────────────────────────────────────
总计：约 7-14s
```
**必须在收到指令后立即回"正在分析..."卡片，避免用户以为无响应。**

### B/S 定时任务（15:05 收盘）
- 单股 7-14s；10只并发约 30-40s（AKShare 有限速，需加 1-2s 间隔）

### S 盘中止损监控
- 单次实时报价 0.3-0.8s + 比价 0.2s，全程 <1s，高频无压力

### 优化手段
- 分析结果缓存 SQLite，同日已分析过的 Q 直接返回缓存（超2小时或收盘后失效）
- AKShare 并发用 asyncio + semaphore 限制同时请求 ≤3

---

## 7. 成本分析

### LLM 成本（10只股票/天，每次~800 tokens，月~0.4M tokens）
| 模型 | 月成本估算 |
|---|---|
| DeepSeek-V3 | ~¥0.6 |
| GPT-4o-mini | ~¥1 |
| Claude Haiku 4.5 | ~¥9 |

**LLM 成本可忽略，选型以中文质量+稳定性为主。**

### 2026 主流模型定价（$/M tokens，输入/输出）
| 模型 | 输入 | 输出 | 中文 |
|---|---|---|---|
| GPT-4o-mini | ~0.15 | ~0.60 | ★★★★★ |
| DeepSeek-V3 | ~0.14 | ~0.28 | ★★★★★ |
| Grok 3 mini | 0.30 | 0.50 | ★★★ |
| Gemini 2.5 Flash | 0.30 | 2.50 | ★★★★ |
| qwen-plus | ~0.40 | ~2.40 | ★★★★★ |
| Claude Haiku 4.5 | 1.00 | 5.00 | ★★★★★ |

### 云服务器
- 阿里云 ECS ecs.e-c1m2.large 2C4G 上海：包年 ¥199/年（≈¥16.6/月）
- 总月均（含LLM+可选域名）：约 ¥25-35/月

---

## 8. 部署与保活

### Docker 部署
```yaml
# docker-compose.yml 核心
services:
  app:
    build: .
    restart: always        # 崩溃自动重启
    volumes:
      - ./data:/app/data    # SQLite 持久化
    env_file: .env
```

### 7×24 保活三重保障
1. Docker `restart: always` — 容器崩溃自动重启
2. APScheduler jobstore=SQLite — 重启后恢复任务状态
3. lark-oapi WebSocket 内置断线重连 + 心跳检测（断线超5分钟飞书告警）

### 部署流程
```
本地 push develop → merge main → SSH登录ECS → git pull → docker-compose up -d --build
```

---

## 9. 关键技术风险

| 风险 | 概率 | 应对 |
|---|---|---|
| AKShare 分钟线延迟/缺失 | 中 | 备选 Tushare（注册免费得200积分可用分钟线）|
| chan.py 对某些股票划分为空（数据不足）| 中 | 最少30根K线，不足时报告"数据不足，建议观望" |
| 飞书 WebSocket 长时间断线 | 低 | 内置重连+心跳，断线超5分钟告警 |
| LLM API 超时 | 低 | 15s超时，超时则返回纯结构化文本（不走LLM）|
