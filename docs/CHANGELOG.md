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
