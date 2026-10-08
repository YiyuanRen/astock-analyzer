# A股缠论股票分析系统 — HANDOVER 交接文档

> **本文件是跨 agent / 跨 host 会话的交接入口。** 新的 agent 会话从这里开始，可零上下文损失地继续工作。

---

## 这是什么项目

通过飞书机器人，用户发送股票编码指令，系统基于**缠论（缠中说禅理论）**对 A 股做**多级别**技术分析，辅助用户判断**买入价和卖出价**，并支持任务式跟踪 + 买卖点出现时主动推送。

**用户的真实场景：** 看好一支股票但不确定精确买卖价，需要系统帮忙"等买点"并给出结构化操作依据（介入价、止损价、目标价、买卖点类型、赢赔比）。

---

## 当前进度

| 阶段 | 状态 | 说明 |
|---|---|---|
| 阶段1 产品方案设计 | ✅ 完成 | 见 [01-product-spec.md](01-product-spec.md) |
| 阶段2 技术方案设计 | ✅ 完成 | 见 [02-tech-design.md](02-tech-design.md) |
| 阶段3 功能实现 | ✅ **已完成并部署上线(ECS Docker)** | Q/B/S/C/L/M 全部实现, 支持私聊+群聊, 定时任务与主动推送, 111 个单测; 2026-10-09 部署到 ECS 并经飞书验证。详见 [05-dev-progress.md](05-dev-progress.md) |
| 阶段4 功能验证 | ⏳ **进行中** | 待观察首个真实交易日定时任务自动触发(盘中B跟踪/止损监控/15:05日报) |

> **接续工作请先读 [05-dev-progress.md](05-dev-progress.md)** —— 它是最新的实现状态与本机环境快照。

---

## 基础设施状态(2026-10-07 更新)

本阶段决定: **先本机跑通**(Windows + PowerShell), ECS 部署推迟。

1. ⬜ 阿里云 ECS（`ecs.e-c1m2.large`，2C4G，上海）—— 筹建中, 暂不需要
2. ✅ 飞书自建应用（App ID/Secret 已入 `.env`, WebSocket 已验证）
3. ✅ LLM API Key（DeepSeek 默认 + MiMo 备用, 均已验证）
4. ✅ Git 远程仓库（`git@github.com:YiyuanRen/astock-analyzer.git`, main+develop）

详细申请步骤见 [03-infra-setup.md](03-infra-setup.md); 实际落地细节与踩坑见 [05-dev-progress.md](05-dev-progress.md)。

---

## 新 agent 的下一步任务

功能已全部完成并**已部署到 ECS(Docker)**, 线上私聊+群验证通过。下一步: 观察首个真实交易日定时任务的自动触发(盘中 B 跟踪 / S 止损监控 / 15:05 日报), 发现问题用 `ssh astock-ecs "docker logs --tail 100 astock-analyzer"` 排查。部署/运维方法见 05-dev-progress.md「ECS 部署与运维」。
> 阶段3 的产品/设计增量决策(群聊会话模型、S 止损规则、提醒节奏、B 盘中时点等)见 05-dev-progress.md「关键设计」。

---

## 关键决策速查（避免推翻已对齐的结论）

这些是经过多轮讨论对齐的决策，**新 agent 不要重新质疑或推翻**，除非用户明确要求变更：

- **缠论实现：** 用开源库 [Vespa314/chan.py](https://github.com/Vespa314/chan.py)，**不用** book-to-skill 把 PDF 转知识图谱（缠论是确定性算法，开源实现更可靠）。PDF（`~/Downloads/market/chan.pdf`，899页）仅作理论参考。
- **数据源：** AKShare（免费、无 token）。
- **多级别联立：** 必须支持（日线定方向，30分/5分定精确入场价），这是缠论"区间套"精髓。
- **LLM 作用：** 只做"结构化计算结果 → 自然语言报告"的组装，**不做缠论计算本身**。
- **概率指标：** 缠论不输出概率，已改为"买卖点类型 + 赢赔比"。
- **部署：** 云服务器 7×24，Docker 容器化（当前先本机跑通，部署推迟）。
- **LLM 可配置：** 支持运行时热切换（飞书 M 指令），模型池支持 openai/anthropic/grok/gemini/qwen/deepseek/kimi/glm/mimo。

### 阶段3 实施期新增/修正的决策（2026-10-07）

- **数据源改用东方财富公开API + curl_cffi**：本机 Clash 代理环境下 requests/urllib3 直连东方财富被 TLS 指纹拦截，改用 curl_cffi(impersonate=chrome) 直连并带重试；功能等价 AKShare 且更稳。
- **MiMo endpoint 修正**：Token Plan(`tp-` key) 正确地址为 `https://token-plan-cn.xiaomimimo.com/v1`（原文档 `api.mimo.ai/v1` 作废）。
- **默认模型 DeepSeek**：DeepSeek 更快；MiMo 可 M 指令切换。LLM 超时放宽到 25s。
- **chan.py vendoring**：源码内置 `vendor/chan.py`(commit 429d6ed)，运行时加 sys.path；多级别区间套由 chan.py 原生 lv_list 支持。

---

## 文档地图

| 文件 | 内容 |
|---|---|
| [HANDOVER.md](HANDOVER.md) | 本文件，交接入口 |
| [01-product-spec.md](01-product-spec.md) | 阶段1：产品方案（报告字段、指令集、任务逻辑、分析频率） |
| [02-tech-design.md](02-tech-design.md) | 阶段2：技术方案（架构、选型、LLM适配层、成本分析） |
| [03-infra-setup.md](03-infra-setup.md) | 阶段3准备：基础设施申请指引 |
| [04-implementation-plan.md](04-implementation-plan.md) | 阶段3：Sprint 行动拆解 |
| [05-dev-progress.md](05-dev-progress.md) | **阶段3 开发进度 & 本机环境快照（最新, 接续工作先读这个）** |

---

## 给新 agent 的提示

1. 先读完 01/02 两份设计文档，建立完整上下文。
2. 阶段3 第一件事是**和用户确认基础设施申请进度**，不要假设已申请。
3. 开发前先初始化项目脚手架（见 02 文档的仓库结构）。
4. 本项目涉及金融分析，所有对外报告都要带风险提示："仅供参考，不构成投资建议"。
