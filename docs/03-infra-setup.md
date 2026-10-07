# 阶段3 准备：基础设施申请指引

> **当前所有基础设施均未申请。** 新 agent 在阶段3第一步必须引导用户逐项申请，拿到凭证后才能开发。
> 每申请完一项，把凭证填入项目根目录的 `.env`（从 `.env.example` 复制），并在本文件勾选 checkbox。

---

## 申请清单总览

| # | 项目 | 状态 | 产出凭证 | 预估耗时 |
|---|---|---|---|---|
| 1 | 阿里云 ECS 服务器 | ⬜ 未申请 | 公网IP、SSH密钥 | 15分钟 |
| 2 | 飞书自建应用 | ⬜ 未申请 | App ID、App Secret | 20分钟 |
| 3 | LLM API Key | ⬜ 未申请 | API Key（至少1家）| 10分钟 |
| 4 | Git 远程仓库 | ⬜ 未申请 | 仓库URL、部署密钥 | 10分钟 |

---

## 1. 阿里云 ECS 服务器 ⬜

**目标机型（已定）：** `ecs.e-c1m2.large`，2 vCPU / 4 GB，华东2（上海）

**申请步骤：**
1. 注册/登录阿里云账号：https://www.aliyun.com
2. 完成实名认证（国内服务器强制要求）
3. 进入 ECS 购买页，优先找包年活动机型（通用算力型，2C4G，¥199/年档位）
   - 若活动机型售罄，选 `ecs.e-c1m2.large` 标准包月
4. 配置：
   - 地域：华东2（上海）
   - 镜像：Ubuntu 22.04 LTS 64位
   - 系统盘：40GB ESSD Entry（够用）
   - 带宽：5 Mbps 按固定带宽
   - 登录方式：**创建并下载 SSH 密钥对**（比密码安全）
5. 安全组放行端口：22（SSH）。**本项目用飞书 WebSocket 长连接，无需放行 80/443**
6. 购买后记录：**公网IP**、保存 **SSH 私钥文件**

**产出填入 `.env` / 本地：**
```
ECS_HOST=<公网IP>
ECS_SSH_KEY=<私钥文件路径，不提交到git>
```

**验证：**
```bash
ssh -i <私钥路径> root@<公网IP>
```

---

## 2. 飞书自建应用 ⬜

**申请步骤：**
1. 登录飞书开放平台：https://open.feishu.cn
2. 「开发者后台」→「创建企业自建应用」，填名称（如"缠论分析助手"）、图标
3. 拿到 **App ID** 和 **App Secret**（凭证与基础信息页）
4. 开通「机器人」能力：应用功能 → 机器人 → 启用
5. 配置权限（权限管理 → 搜索添加）：
   - `im:message` — 读取与发送单聊消息
   - `im:message:send_as_bot` — 以机器人身份发消息
   - `im:message:update_as_bot` — 更新卡片消息（流式思考过程必需）
   - `im:message.p2p_msg:readonly` — 读取用户发给机器人的单聊消息
6. 事件订阅：
   - 订阅方式选 **「使用长连接接收事件」**（无需公网回调URL）
   - 添加事件：`接收消息 im.message.receive_v1`
7. 版本发布：创建版本 → 申请发布（企业内自建应用走管理员审批）
8. 发布后在飞书里搜索到机器人，发起单聊测试

**产出填入 `.env`：**
```
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
```

**注意：** 长连接模式下无需 `encrypt_key` / `verification_token`（那是 webhook 模式才需要）。

---

## 3. LLM API Key ⬜

至少申请一家。推荐优先级：

### 选项A：OpenAI GPT-4o-mini（默认推荐）
- 注册：https://platform.openai.com
- 创建 API Key，充值（国内需境外卡或代充）
- `.env`：`OPENAI_API_KEY=sk-xxxxx`

### 选项B：DeepSeek（国内首选，便宜、无需翻墙）
- 注册：https://platform.deepseek.com
- 创建 API Key，支付宝/微信充值
- `.env`：`DEEPSEEK_API_KEY=sk-xxxxx`，模型名 `deepseek-chat`

### 选项C：阿里 Qwen（和ECS同生态）
- 开通阿里云百炼：https://bailian.console.aliyun.com
- 创建 API Key（DashScope）
- `.env`：`DASHSCOPE_API_KEY=sk-xxxxx`，模型名 `qwen-plus`

### 其余可选（模型池已支持，按需申请）
| Provider | 申请地址 | env 变量 |
|---|---|---|
| Anthropic | console.anthropic.com | ANTHROPIC_API_KEY |
| Grok (xAI) | console.x.ai | XAI_API_KEY |
| Gemini | aistudio.google.com | GEMINI_API_KEY |
| Kimi (月之暗面) | platform.moonshot.cn | MOONSHOT_API_KEY |
| GLM (智谱) | open.bigmodel.cn | ZHIPU_API_KEY |
| MiMo (小米) | 待正式开放 | — |

**建议：** 先配 DeepSeek（国内无门槛）跑通，后续按需加其他家。

---

## 4. Git 远程仓库 ⬜

**申请步骤：**
1. GitHub（https://github.com）或 Gitee（https://gitee.com，国内快）创建**私有仓库** `astock-analyzer`
2. 本地初始化并关联：
   ```bash
   cd astock-analyzer
   git init
   git remote add origin <仓库URL>
   ```
3. 在云服务器生成部署用 SSH key，加到仓库 Deploy Keys（只读）或个人 SSH keys：
   ```bash
   # 在 ECS 上执行
   ssh-keygen -t ed25519 -C "ecs-deploy"
   cat ~/.ssh/id_ed25519.pub   # 复制到仓库 Settings → Deploy keys
   ```
4. 验证 ECS 能拉代码：
   ```bash
   git clone <仓库SSH URL>
   ```

---

## `.env.example` 模板

开发脚手架时在项目根目录创建此文件（真实 `.env` 加入 `.gitignore`）：

```bash
# ===== 飞书 =====
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# ===== LLM（按申请的填，至少一个）=====
OPENAI_API_KEY=
DEEPSEEK_API_KEY=
DASHSCOPE_API_KEY=
ANTHROPIC_API_KEY=
XAI_API_KEY=
GEMINI_API_KEY=
MOONSHOT_API_KEY=
ZHIPU_API_KEY=

# ===== 默认模型（可被飞书 M 指令热切换覆盖）=====
DEFAULT_LLM_PROVIDER=deepseek
DEFAULT_LLM_MODEL=deepseek-chat

# ===== 告警（可选，用于异常推送到自己的飞书）=====
ALERT_FEISHU_WEBHOOK=
```

---

## 申请完成后

全部勾选完毕 → 通知新 agent 进入 [04-implementation-plan.md](04-implementation-plan.md) 的 Sprint 实现阶段。
