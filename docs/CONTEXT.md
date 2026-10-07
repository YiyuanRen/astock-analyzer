# 快速上下文参考卡

> 给新 agent 在不读全部文档的情况下，60秒内建立足够上下文。

---

## 这是什么

A股缠论分析系统 — 用户通过飞书机器人发送股票指令，系统做缠论技术分析并返回买卖信号。

---

## 当前阶段

**阶段1 ✅ 阶段2 ✅ → 阶段3 ⏳ 进行中**

- 环境全部就绪（本机 Windows, Python 3.11 venv, GitHub, 飞书, DeepSeek+MiMo）
- **`Q` 即时查询全链路已打通并飞书验证**（数据→多级别缠论→LLM→飞书卡片）
- 下一步：Sprint 3 任务系统(B/S/C/L)+调度器+止损监控
- **接续工作先读 `05-dev-progress.md`**

---

## 6条飞书指令

```
Q 000001              即时查询
B 000001              创建买入跟踪任务
S 000001 @10.45 100   创建卖出跟踪任务（价格/数量可选）
C 000001              取消任务
L                     列出任务
M [provider] [model]  切换LLM模型
```

---

## 核心技术栈

| 用途 | 方案 |
|---|---|
| 飞书接入 | lark-oapi WebSocket ✅已接 |
| K线数据 | 东方财富公开API + curl_cffi（原AKShare, 因TLS指纹改用）|
| 缠论计算 | Vespa314/chan.py（vendored）|
| LLM | 可热切换，默认 **deepseek-chat**（MiMo 备用）|
| 调度 | APScheduler + SQLite（待做）|
| 部署 | Docker，阿里云 ECS（先本机跑通, 部署推迟）|

---

## 不能推翻的决策

1. 缠论用开源库（chan.py），不用 book-to-skill 把 PDF 转知识图谱
2. LLM 只做"结构化结果→自然语言报告"，不做缠论计算
3. 必须支持多级别联立（日线+30分+5分，区间套）
4. B→S 切换由用户主动发 S 指令触发，系统不假设买入发生
5. 所有报告末尾必须带：⚠️ 仅供参考，不构成投资建议

---

## 文档地图

```
docs/
├── HANDOVER.md              交接入口（本文）
├── CONTEXT.md               本文件（快速参考卡）
├── 01-product-spec.md       产品规格（报告字段、指令集、任务逻辑）
├── 02-tech-design.md        技术方案（架构、选型、LLM适配层代码片段）
├── 03-infra-setup.md        基础设施申请指引（全部未申请！）
├── 04-implementation-plan.md Sprint行动计划（4个Sprint）
└── CHANGELOG.md             变更记录
```
