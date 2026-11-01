"""缠论回测服务与一页 HTML 可视化回测报告渲染器 (/backtests/<id>)。"""
from __future__ import annotations

import asyncio
import html
import json

from app.bot.card_builder import (
    banner_box,
    fmt_pct,
    grey_row,
    kpi_col,
    kpi_row,
    pct_theme,
    schema2_card,
)
from app.config import get_config
from app.data import fetcher
from app.data.market import display_code
from app.engine.backtester import run_chan_backtest
from app.models.database import db_path, get_conn, now_str
from app.services.briefing_service import public_base_url


def backtests_dir():
    d = db_path().parent / "backtests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_backtest_report(report_id: str, code: str, name: str, years: int, summary: dict, html_doc: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO backtest_reports(report_id, stock_code, stock_name, years, summary_json, html_content, created_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(report_id) DO UPDATE SET"
            "  summary_json=excluded.summary_json, html_content=excluded.html_content, created_at=excluded.created_at",
            (report_id, code, name, years, json.dumps(summary, ensure_ascii=False), html_doc, now_str()),
        )
    try:
        (backtests_dir() / f"{report_id}.html").write_text(html_doc, encoding="utf-8")
    except Exception:
        pass


def get_backtest_html(report_id: str) -> str | None:
    p = backtests_dir() / f"{report_id}.html"
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            pass
    with get_conn() as conn:
        row = conn.execute(
            "SELECT html_content FROM backtest_reports WHERE report_id=?", (report_id,)
        ).fetchone()
    if not row:
        return None
    try:
        p.write_text(row["html_content"], encoding="utf-8")
    except Exception:
        pass
    return row["html_content"]


def _svg_equity_chart(curve: list[dict]) -> str:
    if len(curve) < 2:
        return "<p style='color:#64748b;'>数据点不足，暂不展示净值曲线</p>"
    w, h, pad = 860, 240, 28
    vals = [pt["equity"] for pt in curve] + [pt["benchmark"] for pt in curve]
    vmin, vmax = min(vals) * 0.98, max(vals) * 1.02
    if vmax <= vmin:
        vmax = vmin + 1.0

    def _pts(key: str) -> str:
        out = []
        n = len(curve)
        for i, pt in enumerate(curve):
            x = pad + (w - 2 * pad) * i / (n - 1)
            y = h - pad - (h - 2 * pad) * (pt[key] - vmin) / (vmax - vmin)
            out.append(f"{x:.1f},{y:.1f}")
        return " ".join(out)

    eq_pts = _pts("equity")
    bm_pts = _pts("benchmark")
    return f"""
    <svg viewBox="0 0 {w} {h}" style="width:100%;height:auto;background:#fafcff;border:1px solid #e2e8f0;border-radius:10px;">
      <polyline fill="none" stroke="#94a3b8" stroke-width="2" stroke-dasharray="4,4" points="{bm_pts}" />
      <polyline fill="none" stroke="#2563eb" stroke-width="2.8" points="{eq_pts}" />
      <text x="{pad}" y="20" fill="#2563eb" font-size="12" font-weight="bold">━ 缠论策略净值 ({curve[-1]['equity']:,.0f})</text>
      <text x="{pad + 200}" y="20" fill="#64748b" font-size="12">- - 买入持有基准 ({curve[-1]['benchmark']:,.0f})</text>
    </svg>
    """


def render_backtest_html(bt: dict) -> str:
    def _e(v) -> str:
        return html.escape(str(v if v is not None else ""))

    bsp_rows = "".join(
        f"<tr><td><strong>{_e(b['bsp'])}</strong></td><td>{b['count']} 次</td>"
        f"<td>{b['win_rate_pct']:.1f}%</td><td>{b['avg_pnl_pct']:+.2f}%</td></tr>"
        for b in (bt.get("bsp_breakdown") or [])
    ) or "<tr><td colspan='4'>回测区间内未触发完整平仓信号</td></tr>"

    tr_rows = "".join(
        f"<tr><td>{idx}</td><td>{_e(t['buy_date'])}</td><td>{_e(t['buy_bsp'])}</td>"
        f"<td>{t['buy_price']:g} × {t['quantity']}</td><td>{_e(t['sell_date'])}</td>"
        f"<td>{t['sell_price']:g} ({_e(t['sell_reason'])})</td>"
        f"<td style='color:{'#dc2626' if t['pnl_amount']>=0 else '#16a34a'};font-weight:600;'>"
        f"{t['pnl_amount']:+,.2f} ({t['pnl_pct']:+.2f}%)</td><td>{t['hold_days']} 天</td></tr>"
        for idx, t in enumerate(bt.get("trades") or [], 1)
    ) or "<tr><td colspan='8'>暂无历史平仓交割记录</td></tr>"

    chart_svg = _svg_equity_chart(bt.get("equity_curve") or [])
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_e(bt['name'])} ({_e(bt['code'])}) · 缠论策略回测报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background:#f4f6fb; color:#1e293b; margin:0; padding:20px 12px 48px; }}
    .wrap {{ max-width: 1080px; margin: 0 auto; }}
    .hero {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%); color:#fff; border-radius:16px; padding:22px; margin-bottom:20px; }}
    .kpis {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-top:14px; }}
    .kpi {{ background:rgba(255,255,255,0.1); border-radius:10px; padding:12px 14px; }}
    .kpi-label {{ font-size:12px; color:#cbd5e1; }}
    .kpi-val {{ font-size:19px; font-weight:700; margin-top:4px; }}
    .card {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px; margin-bottom:18px; overflow-x:auto; }}
    h2 {{ font-size:17px; margin:0 0 12px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13.5px; }}
    th, td {{ padding:10px 8px; border-bottom:1px solid #f1f5f9; text-align:left; }}
    th {{ background:#f8fafc; color:#475569; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1 style="font-size:21px;margin:0;">📈 {_e(bt['name'])} ({_e(bt['code'])}) · 缠论无未来函数回测报告</h1>
      <p style="font-size:13px;color:#bfdbfe;margin:6px 0 0;">回测区间：{_e(bt['start_date'])} ~ {_e(bt['end_date'])}（共 {bt['bars_count']} 根日线 · 初始本金 {bt['initial_capital']:,.0f} · {'港股T+0' if bt['market']=='HK' else 'A股T+1'}）</p>
      <div class="kpis">
        <div class="kpi"><div class="kpi-label">策略总收益率 / 期末净值</div><div class="kpi-val">{bt['total_return_pct']:+.2f}% ({bt['final_equity']:,.0f})</div></div>
        <div class="kpi"><div class="kpi-label">标的基准涨跌 / 超额 Alpha</div><div class="kpi-val">{bt['benchmark_return_pct']:+.2f}% / {bt['alpha_pct']:+.2f}%</div></div>
        <div class="kpi"><div class="kpi-label">历史胜率 / 交易次数</div><div class="kpi-val">{bt['win_rate_pct']:.1f}% ({bt['win_count']}/{bt['trade_count']})</div></div>
        <div class="kpi"><div class="kpi-label">盈亏比 / 最大回撤</div><div class="kpi-val">{_e(bt['profit_loss_ratio'])} / -{bt['max_drawdown_pct']:.2f}%</div></div>
      </div>
    </div>
    <div class="card">
      <h2>📊 策略净值 vs 买入持有基准曲线（step_load 逐根K线无未来函数）</h2>
      {chart_svg}
    </div>
    <div class="card">
      <h2>🎯 分买点类型（一买 / 二买 / 三买）历史表现拆解</h2>
      <table>
        <thead><tr><th>买点类型</th><th>触发平仓次数</th><th>历史胜率</th><th>单笔平均收益率</th></tr></thead>
        <tbody>{bsp_rows}</tbody>
      </table>
    </div>
    <div class="card">
      <h2>📜 历次模拟交割明细</h2>
      <table>
        <thead><tr><th>#</th><th>买入日期</th><th>买点类型</th><th>买入价 × 股数</th><th>卖出日期</th><th>卖出价(原因)</th><th>单笔盈亏</th><th>持仓K线</th></tr></thead>
        <tbody>{tr_rows}</tbody>
      </table>
    </div>
  </div>
</body>
</html>"""


async def execute_backtest_bt(code: str, years: int = 2) -> dict | str:
    years = max(1, min(int(years or 2), 5))
    disp = display_code(code)
    quote = await asyncio.to_thread(fetcher.fetch_quote, code)
    if not quote or quote.get("price") is None:
        return f"未查询到 {disp} 的行情数据，请确认代码是否正确。"
    name = quote.get("name") or disp
    df = await asyncio.to_thread(fetcher.fetch_daily, code, years)
    bt = await asyncio.to_thread(run_chan_backtest, df, code, name, 100000.0)
    if bt.get("error"):
        return f"⚠️ {disp} {bt['error']}"

    report_id = f"{code}_{years}y"
    html_doc = render_backtest_html(bt)
    save_backtest_report(report_id, code, name, years, bt, html_doc)
    page_url = f"{public_base_url()}/backtests/{report_id}"

    tot_ret = bt["total_return_pct"]
    _, p_bg = pct_theme(tot_ret)
    tpl = "red" if tot_ret > 0 else ("green" if tot_ret < 0 else "blue")
    title = f"📈 缠论历史回测 · {name} {disp}（近{years}年）"
    subtitle = (
        f"策略收益 {tot_ret:+.2f}% (超额 {bt['alpha_pct']:+.2f}%) · "
        f"胜率 {bt['win_rate_pct']:.1f}% ({bt['win_count']}/{bt['trade_count']}) · "
        f"最大回撤 -{bt['max_drawdown_pct']:.2f}%"
    )

    cols = [
        kpi_col("📈 策略总收益", f"{fmt_pct(tot_ret)}",
                f"<font color='grey'>同期基准 {bt['benchmark_return_pct']:+.2f}% · 超额 {bt['alpha_pct']:+.2f}%</font>",
                bg=p_bg),
        kpi_col("🎯 买点胜率", f"**<font color='blue'>{bt['win_rate_pct']:.1f}%</font>**",
                f"<font color='grey'>盈利 {bt['win_count']} 笔 / 共 {bt['trade_count']} 笔</font>",
                bg="blue-50"),
        kpi_col("⚖️ 盈亏比 & 回撤", f"**<font color='violet'>{bt['profit_loss_ratio']}</font>**",
                f"<font color='grey'>最大回撤 -{bt['max_drawdown_pct']:.2f}%</font>",
                bg="violet-50"),
    ]
    elements: list[dict] = [
        kpi_row(cols, flex_mode="trisect"),
        banner_box(
            f"🗺️ **完整净值曲线与交割单报告**：[点击打开《{name} 近{years}年无未来函数回测 HTML 报告》]({page_url})",
            bg="blue-50",
        ),
    ]
    flat_lines = [f"**{title}**\n{subtitle}\n回测报告：{page_url}"]

    if bt.get("bsp_breakdown"):
        elements.append({"tag": "markdown", "content": "#### 🎯 分买点类型胜率统计"})
        elements.append(grey_row([
            ("**买点类型**", 2),
            ("**次数**", 1),
            ("**胜率**", 2),
            ("**平均单笔收益**", 2),
        ]))
        elements.append({"tag": "hr"})
        for b in bt["bsp_breakdown"]:
            elements.append(grey_row([
                (f"**{b['bsp']}**", 2),
                (f"{b['count']} 次", 1),
                (f"**{b['win_rate_pct']:.1f}%**", 2),
                (fmt_pct(b["avg_pnl_pct"]), 2),
            ]))
            flat_lines.append(f"- **{b['bsp']}**：{b['count']} 次，胜率 {b['win_rate_pct']:.1f}%，平均 {b['avg_pnl_pct']:+.2f}%")

    trades = bt.get("trades") or []
    if trades:
        elements.append({"tag": "markdown", "content": "#### 📜 最近平仓交割记录（节选）"})
        for t in trades[-5:]:
            elements.append(grey_row([
                (f"**{t['buy_bsp']}**\n<font color='grey'>{t['buy_date']} → {t['sell_date']}</font>", 3),
                (f"{t['buy_price']:g} → **{t['sell_price']:g}**\n<font color='grey'>{t['sell_reason']}</font>", 3),
                (f"{fmt_pct(t['pnl_pct'])}\n<font color='grey'>持仓 {t['hold_days']} 根K线</font>", 2),
            ]))

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": (
            f"<font color='grey'>基于 chan.py step_load 逐根K线无未来函数回放（初始本金10万 · "
            f"{'港股T+0' if bt['market']=='HK' else 'A股T+1'} · 结构止损只上移不下移） · {get_config().risk_disclaimer}</font>"
        ),
    })
    raw_card = schema2_card(title, subtitle, tpl, elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": tpl,
            "markdown": "\n".join(flat_lines),
            "raw_card": raw_card,
        }
    }
