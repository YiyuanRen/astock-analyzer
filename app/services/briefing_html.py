"""一页 HTML 可视化市场地图渲染器(兼容 PC 浏览器与手机飞书内置浏览器)。

严格遵循用户指定的四段式顺序与结构:
1. 标题栏(含数据源、发布时间、核心统计徽章、往期早报切换入口)
2. 三层漏斗核心要闻 · 四列表(1事件 / 2影响资产 / 3方向[涨/跌/中性/分化] / 4传导逻辑)
   - PC 端为四列表格; 手机端自动切换为高可读性响应式卡片流, 杜绝字小挤压
3. 因果链逐条拆解(通常 3-7 条, 标注「市场共识」或「待验证」):
   - 流程图: [事件A] → [事件B] → ... → [资产价格变动]
   - 术语白话: 每个专业词用一句话讲清
   - 链条类型标签(如 地缘→能源、央行→流动性、AI capex→硬件/信用层、半导体涨价 等)
4. 待验证清单(每条两层):
   - 需思考的细节: 具体盯哪 3-4 个变量
   - 金融历史类比: 真实历史事件 + 一句话教训 + 关键区别
"""
from __future__ import annotations

import html


def _esc(val) -> str:
    return html.escape(str(val or "")).strip()


def _dir_badge_html(direction: str) -> str:
    d = str(direction or "中性").strip()
    if "涨" in d or "多" in d or "升" in d:
        cls = "badge-up"
        icon = "▲ "
    elif "跌" in d or "空" in d or "降" in d:
        cls = "badge-down"
        icon = "▼ "
    elif "分化" in d or "震荡" in d or "波动" in d:
        cls = "badge-mixed"
        icon = "◆ "
    else:
        cls = "badge-neutral"
        icon = "● "
    return f'<span class="dir-badge {cls}">{icon}{_esc(d)}</span>'


def render_market_map_html(
    report_date: str,
    source_title: str,
    source_uri: str,
    published_at: str,
    structured: dict,
) -> str:
    headline = _esc(structured.get("headline_summary") or "全球宏观与核心资产传导全景")
    events = structured.get("events_table") or []
    chains = structured.get("causal_chains") or []
    verifications = structured.get("verification_list") or []

    # 1. 构建四列表行
    table_rows_html = []
    for idx, ev in enumerate(events, 1):
        event_txt = _esc(ev.get("event"))
        asset_txt = _esc(ev.get("asset"))
        dir_badge = _dir_badge_html(ev.get("direction", "中性"))
        logic_txt = _esc(ev.get("logic"))
        table_rows_html.append(
            f"""
            <tr>
              <td data-label="事件" class="col-event"><span class="row-idx">{idx:02d}</span> {event_txt}</td>
              <td data-label="影响资产" class="col-asset"><span class="asset-pill">{asset_txt}</span></td>
              <td data-label="方向" class="col-dir">{dir_badge}</td>
              <td data-label="传导逻辑" class="col-logic">{logic_txt}</td>
            </tr>
            """
        )

    # 2. 构建因果链逐条拆解
    chains_html = []
    for idx, ch in enumerate(chains, 1):
        c_title = _esc(ch.get("title") or f"核心传导链 #{idx}")
        c_type = _esc(ch.get("category") or "宏观传导")
        status_raw = str(ch.get("status") or "市场共识").strip()
        is_consensus = "共识" in status_raw
        status_cls = "tag-consensus" if is_consensus else "tag-verify"
        status_label = "市场共识" if is_consensus else "待验证"

        nodes = ch.get("flow_nodes") or []
        if not nodes and ch.get("flow"):
            nodes = [x.strip() for x in str(ch.get("flow")).split("→") if x.strip()]
        node_html_parts = []
        for n_i, node in enumerate(nodes):
            is_last = (n_i == len(nodes) - 1)
            n_cls = "flow-node flow-node-end" if is_last else "flow-node"
            node_html_parts.append(f'<div class="{n_cls}">{_esc(node)}</div>')
            if not is_last:
                node_html_parts.append('<div class="flow-arrow">→</div>')
        flow_html = "\n".join(node_html_parts)

        jargons = ch.get("jargon_plain") or []
        jargon_items = []
        for j in jargons:
            if isinstance(j, dict):
                term = _esc(j.get("term"))
                exp = _esc(j.get("explanation"))
                jargon_items.append(f'<li><strong>{term}</strong>：{exp}</li>')
            else:
                jargon_items.append(f"<li>{_esc(j)}</li>")
        jargon_html = "\n".join(jargon_items) if jargon_items else "<li>暂无特殊专业术语</li>"

        chains_html.append(
            f"""
            <div class="chain-card">
              <div class="chain-header">
                <div class="chain-title-wrap">
                  <span class="chain-num">链条 {idx}</span>
                  <h3 class="chain-title">{c_title}</h3>
                </div>
                <div class="chain-badges">
                  <span class="chain-type">{c_type}</span>
                  <span class="chain-status {status_cls}">{status_label}</span>
                </div>
              </div>
              <div class="chain-section-label">① 因果传导流程图</div>
              <div class="flow-container">
                {flow_html}
              </div>
              <div class="chain-section-label">② 术语白话（一句话讲清）</div>
              <ul class="jargon-list">
                {jargon_html}
              </ul>
            </div>
            """
        )

    # 3. 构建待验证清单
    verify_html = []
    for idx, vf in enumerate(verifications, 1):
        v_topic = _esc(vf.get("topic") or f"关键验证议题 #{idx}")
        vars_list = vf.get("watch_variables") or []
        vars_li = "\n".join(f"<li>{_esc(v)}</li>" for v in vars_list)

        analogy = vf.get("historical_analogy") or {}
        if isinstance(analogy, dict):
            h_event = _esc(analogy.get("event") or "历史参照")
            h_lesson = _esc(analogy.get("lesson") or "")
            h_diff = _esc(analogy.get("difference") or "")
            analogy_block = f"""
              <div class="analogy-box">
                <div class="analogy-Row"><span class="analogy-tag">历史事件</span> <strong>{h_event}</strong></div>
                <div class="analogy-Row"><span class="analogy-tag">核心教训</span> {h_lesson}</div>
                <div class="analogy-Row"><span class="analogy-tag">关键区别</span> {h_diff}</div>
              </div>
            """
        else:
            analogy_block = f'<div class="analogy-box">{_esc(analogy)}</div>'

        verify_html.append(
            f"""
            <div class="verify-card">
              <div class="verify-title">🔍 {idx}. {v_topic}</div>
              <div class="verify-grid">
                <div class="verify-col">
                  <div class="verify-sub">🎯 需思考的细节（重点盯盘变量）</div>
                  <ul class="watch-list">{vars_li}</ul>
                </div>
                <div class="verify-col">
                  <div class="verify-sub">📜 金融历史类比</div>
                  {analogy_block}
                </div>
              </div>
            </div>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0" />
  <title>{_esc(report_date)} 早餐 · 全球市场可视化地图</title>
  <style>
    :root {{
      --bg: #f4f6fb;
      --card-bg: #ffffff;
      --text-main: #1a202c;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --primary: #2563eb;
      --up-bg: #fef2f2;
      --up-text: #dc2626;
      --up-border: #fecaca;
      --down-bg: #f0fdf4;
      --down-text: #16a34a;
      --down-border: #bbf7d0;
      --mix-bg: #fff7ed;
      --mix-text: #ea580c;
      --mix-border: #fed7aa;
      --neu-bg: #f1f5f9;
      --neu-text: #475569;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text-main);
      line-height: 1.65;
      padding: 16px 12px 48px;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{ max-width: 1080px; margin: 0 auto; }}
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
      color: #fff;
      border-radius: 16px;
      padding: 24px 22px;
      margin-bottom: 20px;
      box-shadow: 0 10px 25px rgba(15, 23, 42, 0.12);
    }}
    .hero-top {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      font-size: 13px;
      color: #93c5fd;
      margin-bottom: 10px;
    }}
    .hero-top a {{
      color: #bfdbfe;
      text-decoration: none;
      background: rgba(255,255,255,0.12);
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
    }}
    .hero h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 10px; letter-spacing: 0.3px; }}
    .hero-summary {{
      background: rgba(255,255,255,0.1);
      border-left: 4px solid #60a5fa;
      padding: 10px 14px;
      border-radius: 8px;
      font-size: 14.5px;
      color: #f8fafc;
      margin-bottom: 14px;
    }}
    .hero-stats {{ display: flex; gap: 10px; flex-wrap: wrap; font-size: 12.5px; }}
    .stat-pill {{
      background: rgba(15, 23, 42, 0.45);
      border: 1px solid rgba(148, 163, 184, 0.3);
      padding: 4px 12px;
      border-radius: 999px;
      color: #e2e8f0;
    }}
    .section-block {{
      background: var(--card-bg);
      border-radius: 14px;
      padding: 20px 18px;
      margin-bottom: 20px;
      box-shadow: 0 2px 10px rgba(15, 23, 42, 0.04);
      border: 1px solid var(--border);
    }}
    .section-header {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 16px;
      padding-bottom: 10px;
      border-bottom: 2px solid #f1f5f9;
    }}
    .section-title {{ font-size: 18px; font-weight: 700; color: #0f172a; }}
    .section-sub {{ font-size: 12.5px; color: var(--text-muted); }}

    /* 四列表样式 */
    .events-table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    .events-table th {{
      background: #f8fafc;
      color: #475569;
      font-weight: 600;
      text-align: left;
      padding: 11px 12px;
      border-bottom: 2px solid var(--border);
      white-space: nowrap;
    }}
    .events-table td {{
      padding: 12px;
      border-bottom: 1px solid #f1f5f9;
      vertical-align: top;
    }}
    .events-table tr:last-child td {{ border-bottom: none; }}
    .col-event {{ width: 28%; font-weight: 600; color: #1e293b; }}
    .col-asset {{ width: 18%; }}
    .col-dir {{ width: 12%; white-space: nowrap; }}
    .col-logic {{ width: 42%; color: #334155; }}
    .row-idx {{
      display: inline-block;
      background: #e2e8f0;
      color: #475569;
      font-size: 11px;
      font-weight: 700;
      padding: 1px 6px;
      border-radius: 4px;
      margin-right: 4px;
    }}
    .asset-pill {{
      display: inline-block;
      background: #eff6ff;
      color: #1d4ed8;
      border: 1px solid #bfdbfe;
      padding: 2px 8px;
      border-radius: 6px;
      font-size: 12.5px;
      font-weight: 600;
    }}
    .dir-badge {{
      display: inline-block;
      padding: 2px 9px;
      border-radius: 999px;
      font-size: 12.5px;
      font-weight: 700;
    }}
    .badge-up {{ background: var(--up-bg); color: var(--up-text); border: 1px solid var(--up-border); }}
    .badge-down {{ background: var(--down-bg); color: var(--down-text); border: 1px solid var(--down-border); }}
    .badge-mixed {{ background: var(--mix-bg); color: var(--mix-text); border: 1px solid var(--mix-border); }}
    .badge-neutral {{ background: var(--neu-bg); color: var(--neu-text); border: 1px solid #cbd5e1; }}

    /* 因果链卡片 */
    .chain-card {{
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
      background: #fafcff;
    }}
    .chain-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 12px;
    }}
    .chain-title-wrap {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
    .chain-num {{
      background: #1e293b;
      color: #fff;
      font-size: 11.5px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 6px;
    }}
    .chain-title {{ font-size: 16px; font-weight: 700; color: #0f172a; }}
    .chain-badges {{ display: flex; gap: 6px; align-items: center; }}
    .chain-type {{
      background: #f1f5f9;
      color: #475569;
      font-size: 12px;
      padding: 2px 8px;
      border-radius: 6px;
      font-weight: 500;
    }}
    .chain-status {{
      font-size: 12px;
      font-weight: 700;
      padding: 2px 9px;
      border-radius: 999px;
    }}
    .tag-consensus {{ background: #ede9fe; color: #6d28d9; border: 1px solid #ddd6fe; }}
    .tag-verify {{ background: #fef3c7; color: #b45309; border: 1px solid #fde68a; }}
    .chain-section-label {{
      font-size: 12.5px;
      font-weight: 700;
      color: #475569;
      margin: 10px 0 6px;
    }}
    .flow-container {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 6px;
      background: #ffffff;
      border: 1px dashed #cbd5e1;
      padding: 12px;
      border-radius: 10px;
    }}
    .flow-node {{
      background: #f8fafc;
      border: 1px solid #cbd5e1;
      color: #1e293b;
      padding: 6px 11px;
      border-radius: 8px;
      font-size: 13.5px;
      font-weight: 500;
    }}
    .flow-node-end {{
      background: #eff6ff;
      border: 1.5px solid #3b82f6;
      color: #1e40af;
      font-weight: 700;
    }}
    .flow-arrow {{ color: #64748b; font-weight: 700; font-size: 16px; padding: 0 2px; }}
    .jargon-list {{
      list-style: none;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 10px 14px;
      font-size: 13.5px;
      color: #334155;
    }}
    .jargon-list li {{ padding: 4px 0; border-bottom: 1px dashed #f1f5f9; }}
    .jargon-list li:last-child {{ border-bottom: none; }}

    /* 待验证清单 */
    .verify-card {{
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
      background: #fffdf9;
    }}
    .verify-title {{ font-size: 15.5px; font-weight: 700; color: #0f172a; margin-bottom: 10px; }}
    .verify-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .verify-col {{
      background: #ffffff;
      border: 1px solid #f1f5f9;
      border-radius: 10px;
      padding: 12px 14px;
    }}
    .verify-sub {{ font-size: 13px; font-weight: 700; color: #334155; margin-bottom: 8px; }}
    .watch-list {{ padding-left: 18px; font-size: 13.5px; color: #334155; }}
    .watch-list li {{ margin-bottom: 5px; }}
    .analogy-box {{ font-size: 13.5px; color: #334155; display: flex; flex-direction: column; gap: 6px; }}
    .analogy-tag {{
      display: inline-block;
      background: #f1f5f9;
      color: #475569;
      font-size: 11.5px;
      font-weight: 600;
      padding: 1px 6px;
      border-radius: 4px;
      margin-right: 4px;
    }}
    .footer {{
      text-align: center;
      font-size: 12.5px;
      color: var(--text-muted);
      margin-top: 24px;
      padding-top: 12px;
    }}

    /* 手机飞书浏览器窄屏自适应优化 (< 768px) */
    @media (max-width: 768px) {{
      body {{ padding: 10px 8px 36px; }}
      .hero {{ padding: 18px 14px; border-radius: 12px; }}
      .hero h1 {{ font-size: 18.5px; }}
      .section-block {{ padding: 14px 12px; border-radius: 12px; }}
      .events-table thead {{ display: none; }}
      .events-table, .events-table tbody, .events-table tr, .events-table td {{
        display: block;
        width: 100%;
      }}
      .events-table tr {{
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 10px 12px;
        margin-bottom: 10px;
        background: #fafcff;
      }}
      .events-table td {{
        padding: 5px 0;
        border-bottom: none;
      }}
      .events-table td::before {{
        content: attr(data-label) "：";
        font-weight: 600;
        color: #64748b;
        font-size: 12.5px;
      }}
      .verify-grid {{ grid-template-columns: 1fr; gap: 10px; }}
      .flow-container {{ flex-direction: column; align-items: stretch; }}
      .flow-arrow {{ text-align: center; transform: rotate(90deg); line-height: 1; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="hero">
      <div class="hero-top">
        <span>📡 数据源：{_esc(source_title)} · 发布于 {_esc(published_at)}</span>
        <div>
          <a href="{_esc(source_uri)}" target="_blank" rel="noopener">见闻原文 ↗</a>
          <a href="/briefings" style="margin-left:6px;">🗂️ 往期归档</a>
        </div>
      </div>
      <h1>🗺️ {_esc(report_date)} 早餐 · 全球市场可视化地图</h1>
      <div class="hero-summary">💡 <strong>今日核心主线</strong>：{headline}</div>
      <div class="hero-stats">
        <span class="stat-pill">📌 核心要闻 {len(events)} 条（三层漏斗精选）</span>
        <span class="stat-pill">🔗 因果传导链 {len(chains)} 条</span>
        <span class="stat-pill">🔍 待验证清单 {len(verifications)} 项</span>
      </div>
    </header>

    <section class="section-block">
      <div class="section-header">
        <h2 class="section-title">一、三层漏斗核心要闻（四列表骨架）</h2>
        <span class="section-sub">仅保留价格异动 / 超预期新信息 / 宏观与核心资产相关要闻</span>
      </div>
      <table class="events-table">
        <thead>
          <tr>
            <th>1. 核心事件</th>
            <th>2. 影响资产</th>
            <th>3. 方向</th>
            <th>4. 传导逻辑</th>
          </tr>
        </thead>
        <tbody>
          {"".join(table_rows_html)}
        </tbody>
      </table>
    </section>

    <section class="section-block">
      <div class="section-header">
        <h2 class="section-title">二、核心因果链逐条拆解</h2>
        <span class="section-sub">流程图传导路径 + 专业术语一句话白话 + 市场共识/待验证界定</span>
      </div>
      {"".join(chains_html)}
    </section>

    <section class="section-block">
      <div class="section-header">
        <h2 class="section-title">三、待验证清单 & 金融历史类比</h2>
        <span class="section-sub">具体盯盘观察变量 + 真实历史事件教训与当下关键区别</span>
      </div>
      {"".join(verify_html)}
    </section>

    <footer class="footer">
      数据源：华尔街见闻早餐 FM-Radio · 由智能投研助手生成 · ⚠️ 仅供参考，不构成投资建议
    </footer>
  </div>
</body>
</html>
"""


def render_archive_html(items: list[dict]) -> str:
    """渲染 /briefings 历史早报归档目录页(重启后所有历史早报均可在此查阅)。"""
    rows = []
    for it in items:
        d = _esc(it.get("report_date"))
        hl = _esc(it.get("headline") or it.get("source_title") or "")
        ev_c = it.get("events_count", 0)
        ch_c = it.get("chains_count", 0)
        rows.append(
            f"""
            <a class="arc-item" href="/briefings/{d}">
              <div class="arc-date">📅 {d} 早餐</div>
              <div class="arc-hl">{hl}</div>
              <div class="arc-meta">核心要闻 {ev_c} 条 · 因果链 {ch_c} 条 →</div>
            </a>
            """
        )
    body = "\n".join(rows) if rows else "<p style='color:#64748b;'>暂无已生成的历史早报记录。</p>"
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>华尔街见闻早餐 · 历史市场地图归档</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: #f4f6fb; color: #1e293b; padding: 24px 14px; margin: 0;
    }}
    .wrap {{ max-width: 820px; margin: 0 auto; }}
    h1 {{ font-size: 22px; margin-bottom: 6px; }}
    .sub {{ color: #64748b; font-size: 13.5px; margin-bottom: 20px; }}
    .arc-item {{
      display: block; text-decoration: none; color: inherit;
      background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
      padding: 16px 18px; margin-bottom: 12px;
      transition: transform 0.15s, box-shadow 0.15s;
    }}
    .arc-item:hover {{ transform: translateY(-2px); box-shadow: 0 6px 16px rgba(15,23,42,0.08); }}
    .arc-date {{ font-weight: 700; font-size: 16px; color: #1d4ed8; margin-bottom: 4px; }}
    .arc-hl {{ font-size: 14px; color: #334155; margin-bottom: 6px; }}
    .arc-meta {{ font-size: 12px; color: #64748b; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>🗂️ 市场信息早报 · 历史可视化地图归档</h1>
    <div class="sub">点击任意日期即可查看当日【一页 HTML 可视化市场地图】</div>
    {body}
  </div>
</body>
</html>
"""
