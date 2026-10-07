# 阶段3：Sprint 实现计划

> **前提：** 基础设施全部就绪（见 [03-infra-setup.md](03-infra-setup.md)），所有凭证填入 `.env`。

---

## Sprint 总览

| Sprint | 内容 | 前置条件 |
|---|---|---|
| Sprint 1 | 项目脚手架 + 飞书接入 + 数据通路验证 | ECS + 飞书应用 + Git 仓库就绪 |
| Sprint 2 | 缠论分析引擎（单级别 → 多级别） | Sprint 1 完成 |
| Sprint 3 | 任务系统 + 调度器 + 止损监控 | Sprint 2 完成 |
| Sprint 4 | LLM报告生成 + 飞书卡片排版 + 上线 | Sprint 3 完成 |

---

## Sprint 1：脚手架 + 飞书接入 + 数据通路（第1周）

### 任务1.1 初始化项目结构
```bash
mkdir -p astock-analyzer/app/{bot,commands,engine,data,llm/providers,scheduler,models}
mkdir -p astock-analyzer/{config,tests,docker,docs}
cd astock-analyzer && git init
```

创建文件：
- `requirements.txt`（lark-oapi、akshare、apscheduler、chan.py依赖、openai、anthropic、pyyaml、python-dotenv）
- `config/config.yaml`（LLM配置、调度配置）
- `config/.env.example`（所有环境变量模板）
- `.gitignore`（排除 `.env`、`*.db`、`__pycache__`、`data/*.db`）
- `docker/Dockerfile`
- `docker/docker-compose.yml`

### 任务1.2 飞书 WebSocket 接入
实现 `app/bot/feishu_bot.py`：
- lark-oapi WebSocket 长连接
- 注册 `im.message.receive_v1` 事件
- 收到消息 → 解析文本 → 路由到命令解析层
- 断线重连 + 心跳检测（断线超5分钟推送告警到私聊）

验证：在飞书中向机器人发送任意消息，终端打印出来即成功。

### 任务1.3 命令解析层
实现 `app/commands/parser.py`：
```python
# 正则解析6条指令
Q_PATTERN = r'^Q\s+(\d{6})$'
B_PATTERN = r'^B\s+(\d{6})$'
S_PATTERN = r'^S\s+(\d{6})(?:\s+@([\d.]+))?(?:\s+(\d+))?$'
C_PATTERN = r'^C\s+(\d{6})$'
L_PATTERN = r'^L$'
M_PATTERN = r'^M(?:\s+(\w+)\s+(\S+))?$'
```
未匹配 → 返回帮助提示（列出6条指令格式）。

### 任务1.4 AKShare 数据拉取层
实现 `app/data/fetcher.py`：
- `fetch_daily(code, years=3)` — 日线K线
- `fetch_30min(code, months=6)` — 30分钟K线
- `fetch_5min(code, months=1)` — 5分钟K线
- `fetch_realtime_price(code)` — 实时报价（盘中止损用）
- asyncio 并发拉取三周期数据，semaphore 限制 ≤3 并发
- 统一返回 pandas DataFrame（列：date, open, high, low, close, volume）

验证：拉取平安银行（000001）三个周期数据，打印 head(5)。

---

## Sprint 2：缠论分析引擎（第2周）

### 任务2.1 单级别缠论分析
实现 `app/engine/chan_analyzer.py`，封装 chan.py：

```python
from chan import Chan, KLine_Unit

class ChanAnalyzer:
    def analyze(self, df: pd.DataFrame, level: str = "日线") -> dict:
        """
        输入：OHLCV DataFrame
        输出：结构化缠论结果字典
        {
            "bi_direction": "向上/向下",
            "segment_direction": "上升/下降/震荡",
            "zhongshu_range": (low, high),
            "price_vs_zhongshu": "上方/内部/下方",
            "buy_sell_point": "一买/二买/三买/一卖/二卖/三卖/无信号",
            "macd_divergence": True/False,
            "bi_bars": 12,
        }
        """
```

**注意：** chan.py 最少需要约30根K线才能形成有效分析。不足时返回 `{"error": "数据不足，建议观望"}`。

### 任务2.2 多级别联立（区间套）
实现 `app/engine/multi_level.py`：

```python
def multi_level_analyze(daily_df, m30_df, m5_df) -> dict:
    """
    1. 先用日线确定方向和买卖点类型
    2. 日线有买点 → 用30分钟确认买点是否同级别共振
    3. 30分钟确认 → 用5分钟找精确入场点位
    4. 返回精确的建议介入价区间
    """
```

多级别结论示例输出：
```python
{
    "daily": {"buy_sell_point": "三买", "zhongshu_range": (10.2, 10.8)},
    "m30":   {"confirmed": True, "entry_zone": (10.3, 10.5)},
    "m5":    {"precise_entry": 10.42, "stop_loss": 9.95},
    "target": 11.8,
    "rr_ratio": "1:2.3",
    "position_advice": "三买确认，建议仓位30-50%",
}
```

### 任务2.3 报告结构化输出
实现 `app/engine/report_builder.py`：
将多级别分析结果 + 实时价格 + 成交量信息 → 组装为标准报告字典（对应产品规格的所有字段）。

验证：对5支不同状态的股票（上涨/下跌/震荡/数据不足/买点出现）各跑一次，检查输出合理性。

---

## Sprint 3：任务系统 + 调度 + 止损监控（第3周）

### 任务3.1 SQLite 数据模型
实现 `app/models/database.py`，建表：

```sql
-- 活跃任务表
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    stock_code TEXT NOT NULL,
    task_type TEXT NOT NULL,     -- 'B' 或 'S'
    status TEXT DEFAULT 'active',
    buy_price REAL,              -- S任务可选
    buy_quantity INTEGER,        -- S任务可选
    stop_loss REAL,              -- 分析引擎计算填入
    target_price REAL,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(stock_code)           -- 同一股票只能一个任务
);

-- 当前LLM配置（热切换用）
CREATE TABLE llm_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    updated_at TEXT
);

-- 分析结果缓存
CREATE TABLE analysis_cache (
    stock_code TEXT PRIMARY KEY,
    result_json TEXT,
    analyzed_at TEXT
);
```

### 任务3.2 B/S/C/L/M 指令完整实现

- `B`: 创建B任务 → 立即触发一次分析 → 返回初始报告
- `S`: 创建S任务（若有B任务先取消）→ 记录买入价/数量（可选）→ 返回持仓状态
- `C`: 取消该股任务 → 确认消息
- `L`: 查询所有活跃任务 → 格式化列表（含每支股票状态摘要）
- `M`: 无参数=查看当前模型；有参数=更新 llm_config 表，立即生效

### 任务3.3 APScheduler 调度器
实现 `app/scheduler/jobs.py`：

```python
# 每交易日15:05 触发所有活跃任务的分析
scheduler.add_job(
    daily_analysis_job,
    CronTrigger(day_of_week='mon-fri', hour=15, minute=5,
                timezone='Asia/Shanghai'),
    id='daily_analysis'
)

# S任务盘中止损监控（交易时间9:30-15:00，每3分钟）
scheduler.add_job(
    stop_loss_monitor_job,
    IntervalTrigger(minutes=3),
    id='stop_loss_monitor'
)
```

**止损监控逻辑：**
```python
async def stop_loss_monitor_job():
    s_tasks = db.get_active_s_tasks()
    for task in s_tasks:
        price = await fetcher.fetch_realtime_price(task.stock_code)
        if price <= task.stop_loss:
            await bot.send_alert(task.user_id,
                f"⚠️ {task.stock_code} 当前价 {price}，已触及止损位 {task.stop_loss}，请注意！")
```

### 任务3.4 B任务精确入场跟踪
当日线分析出现买点信号时，激活低级别30分钟跟踪（每30分钟运行一次）：

```python
# 动态添加/移除跟踪任务
def activate_intraday_tracking(stock_code: str):
    scheduler.add_job(
        intraday_tracking_job,
        IntervalTrigger(minutes=30),
        id=f'intraday_{stock_code}',
        args=[stock_code]
    )
```

---

## Sprint 4：LLM 报告生成 + 飞书卡片 + 上线（第4周）

### 任务4.1 LLM 适配层实现
实现 `app/llm/` 完整代码（参见 02-tech-design.md 第4节代码片段）：
- `base.py`：BaseLLMClient 抽象类
- `factory.py`：get_llm_client 工厂 + PROVIDER_BASE_URLS
- `config_store.py`：SQLite 读写模型配置
- `providers/anthropic_client.py`
- `providers/openai_compatible.py`（含 stream_chat 流式方法）

### 任务4.2 报告生成 Prompt
实现 `app/llm/report_generator.py`：

```python
SYSTEM_PROMPT = """
你是一个专业的缠论技术分析助手。
根据输入的结构化分析数据，生成清晰、专业的股票分析报告。
报告使用中文，语言简洁，避免主观预测，数据客观呈现。
每份报告最后必须包含：⚠️ 仅供参考，不构成投资建议。
"""

def build_user_prompt(analysis: dict) -> str:
    # 将结构化分析结果转为 LLM 输入文本
    ...
```

**降级策略：** LLM 调用超时（15s）时，直接用结构化数据渲染纯文本报告，不依赖 LLM。

### 任务4.3 飞书卡片消息设计

**分析中卡片（立即发送）：**
```json
{
  "header": "📊 正在分析 000001 平安银行...",
  "elements": [{"tag": "markdown", "content": "数据获取与缠论计算中，预计 10-15 秒..."}]
}
```

**完整报告卡片：**
```
┌──────────────────────────────────┐
│ 📊 000001 平安银行  |  2026-10-07 │
├──────────────────────────────────┤
│ 💭 思考过程 ∨（可选，默认折叠）   │← 仅分析超10s时启用
│   • 识别笔段: 当前向上笔...       │
│   • 中枢计算: 10.2-10.8...       │
│   • 多级别联立: 日线三买...       │
├──────────────────────────────────┤
│ 当前价：10.48  涨跌：+1.2%       │
│ ─────────────────────────────── │
│ 【缠论信号】                     │
│ 趋势：上升  |  笔方向：向上       │
│ 中枢：10.2-10.8（当前在上方）    │
│ 信号：✅ 三类买点（日线确认）     │
│ MACD背驰：是  |  笔持续：8天     │
│ ─────────────────────────────── │
│ 【操作建议】                     │
│ 方向：买入  |  仓位：30-50%      │
│ 介入价：10.40-10.55              │
│ 止损：9.95  |  目标：11.80      │
│ 赢赔比：1:2.3                   │
│ ─────────────────────────────── │
│ 支撑：10.2  压力：11.0          │
│ 成交量：放量                    │
│ ─────────────────────────────── │
│ ⚠️ 仅供参考，不构成投资建议      │
└──────────────────────────────────┘
```

### 任务4.4 思考过程（⛔ 暂不实现）
**等待用户明确指示后再实现。** 飞书卡片流式更新的技术方案已在 02-tech-design.md 中完整记录，届时直接参照实现即可。

### 任务4.5 端到端测试清单

- [ ] `Q 000001` — 返回完整报告
- [ ] `B 000001` — 创建任务，返回初始分析
- [ ] `S 000001 @10.45 100` — B→S切换，记录买入信息
- [ ] `C 000001` — 取消任务
- [ ] `L` — 列出活跃任务
- [ ] `M deepseek deepseek-chat` — 切换模型，再次 Q 验证切换生效
- [ ] 盘中止损监控：手动修改 stop_loss 到高于当前价，验证告警推送
- [ ] 定时任务：手动触发 daily_analysis_job，验证推送
- [ ] LLM超时降级：mock LLM超时，验证返回纯结构化报告
- [ ] 数据不足：用新股代码测试，验证返回"数据不足"提示

### 任务4.6 部署到云服务器

```bash
# 本地
git push origin main

# SSH登录ECS
ssh -i <私钥> root@<公网IP>
git clone <仓库SSH URL>
cd astock-analyzer
cp config/.env.example .env
# 填入真实凭证
nano .env
docker-compose up -d --build

# 验证
docker-compose logs -f app
```

---

## 关键约束备忘

- AKShare 频率限制：并发 ≤3，请求间隔 1-2s
- 飞书 patch API 频率：≤5次/秒
- 飞书卡片内容大小：≤30KB
- chan.py 最少需要 30 根K线
- LLM 调用超时：15s，超时走降级
- A股交易时间：9:30-11:30、13:00-15:00（止损监控只在此窗口运行）
- 所有报告末尾必须带：⚠️ 仅供参考，不构成投资建议
