"""飞书消息卡片构建器 (Schema 2.0, 遵循 docs/Feishu_Card_Style.md 规范)。

实现四类核心卡片:
- A1 股票分析主卡片 (build_analysis_card): 顶栏彩色 KPI(观望态 3 列自动省去无内容的入场/止损/目标;
  买卖信号态 4 列作战卡) + 2 行灰底紧凑缠论结构条 + LLM 深度解读与操作预案
- B1 持仓跟踪卡片 (build_holding_card): 双排彩色 KPI(持仓账本 + 风控锚点, 未填成本自动隐藏第 1 排)
  + 缠论结构紧凑条 + LLM 深度解读
- C1 任务列表卡片 (build_task_list_card): column_set 灰底多列表格, 异常/临近止损自动置顶加 ❗
- D1 盘中止损告警卡片 (build_stop_alert_card): 三列冲击 KPI + 盘中应对参考

涨跌与盈亏配色遵循 A股/港股「红涨绿跌、红盈绿亏」习惯:
- 正值(>0): <font color='red'> + red-50 背景
- 负值(<0): <font color='green'> + green-50 背景
- 零值/中性: <font color='grey'> + grey-50 背景
"""
from __future__ import annotations

from typing import Any

from app.data.market import daily_report_time_str, display_code
from app.engine import holding


# ---------------- 基础颜色与格式化原语 ----------------
def pct_theme(val: float | None) -> tuple[str, str]:
    """按红涨绿跌返回 (文字颜色关键字, KPI列背景色块)。"""
    if val is None or val == 0:
        return "grey", "grey-50"
    return ("red", "red-50") if val > 0 else ("green", "green-50")


def fmt_pct(val: float | None, bold: bool = True) -> str:
    """格式化涨跌幅/盈亏百分比并附带红涨绿跌 <font> 标签。"""
    if val is None:
        return "<font color='grey'>—</font>"
    col, _ = pct_theme(val)
    inner = f"{val:+.2f}%"
    return f"<font color='{col}'>**{inner}**</font>" if bold else f"<font color='{col}'>{inner}</font>"


def fmt_amt(val: float | None, bold: bool = True) -> str:
    """格式化带符号金额并附带红盈绿亏颜色。"""
    if val is None:
        return ""
    col, _ = pct_theme(val)
    inner = f"{val:+,.2f}"
    return f"<font color='{col}'>**{inner}**</font>" if bold else f"<font color='{col}'>{inner}</font>"


def kpi_col(label: str, value_md: str, sub_md: str = "", bg: str = "grey-50", weight: int = 1) -> dict:
    """单个 KPI 语义色块列(三行结构)。"""
    lines = [f"<font color='#666666'>{label}</font>", value_md]
    if sub_md:
        lines.append(sub_md)
    return {
        "tag": "column",
        "width": "weighted",
        "weight": weight,
        "background_style": bg,
        "padding": "8px",
        "vertical_align": "top",
        "elements": [{"tag": "markdown", "content": "\n".join(lines)}],
    }


def kpi_row(columns: list[dict], flex_mode: str = "trisect") -> dict:
    return {
        "tag": "column_set",
        "flex_mode": flex_mode,
        "background_style": "default",
        "horizontal_spacing": "default",
        "columns": columns,
    }


def grey_row(cells: list[tuple[str, int]]) -> dict:
    """background_style=grey 的多列紧凑行。cells=[(markdown_text, weight), ...]"""
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "grey",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": w,
                "vertical_align": "center",
                "elements": [{"tag": "markdown", "content": text}],
            }
            for text, w in cells
        ],
    }


def banner_box(markdown_text: str, bg: str = "blue-50") -> dict:
    """顶部状态横幅色块(用于 B/S 任务创建回执、收盘日报或盘中预警头部)。"""
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "background_style": "default",
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "background_style": bg,
                "padding": "8px",
                "vertical_align": "center",
                "elements": [{"tag": "markdown", "content": markdown_text}],
            }
        ],
    }


def schema2_card(title: str, subtitle: str, template: str, elements: list[dict]) -> dict:
    header: dict[str, Any] = {
        "template": template,
        "title": {"tag": "plain_text", "content": title},
    }
    if subtitle:
        header["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": header,
        "body": {"elements": elements},
    }


# ---------------- 缠论结构紧凑条(A1 / B1 共用) ----------------
def _chan_structure_rows(report: dict) -> tuple[list[dict], str]:
    c = report.get("chan_signal") or {}
    seg_dir = c.get("segment_direction") or "—"
    bi_dir = c.get("bi_direction") or "—"
    bi_bars = c.get("bi_bars")
    bi_txt = f"{bi_dir}（{bi_bars}根K线）" if bi_bars is not None else bi_dir

    bsp = c.get("buy_sell_point") or "无信号"
    macd = "已出现" if c.get("macd_divergence") else "未出现"
    if bsp != "无信号":
        bsp_md = f"<font color='red'>**{bsp}**</font>" if "买" in bsp else f"<font color='green'>**{bsp}**</font>"
    else:
        bsp_md = "无信号"

    zs = c.get("zhongshu") or "未形成显著中枢"
    ml_conc = c.get("multi_level_conclusion") or "—"

    row1 = grey_row([
        (f"**📈 趋势(线段)**：{seg_dir}", 1),
        (f"**✏️ 当前笔**：{bi_txt}", 1),
        (f"**⚡ 买卖点/背驰**：{bsp_md} · 背驰{macd}", 1),
    ])
    row2 = grey_row([
        (f"**🧭 中枢位置**：{zs}\n**🔗 多级别联立**：{ml_conc}", 1),
    ])
    summary = (
        f"趋势(线段)：{seg_dir} ｜ 笔方向：{bi_txt} ｜ 买卖点：{bsp} ｜ 背驰：{macd}\n"
        f"中枢：{zs} ｜ 多级别结论：{ml_conc}"
    )
    return [row1, row2], summary


def _footer_element(report: dict, cached_hhmm: str | None = None) -> tuple[dict, str]:
    b = report.get("basic") or {}
    mkt = b.get("market") or "A股"
    cur = b.get("currency") or "CNY"
    t_str = b.get("time") or ""
    disclaimer = report.get("disclaimer") or "⚠️ 仅供参考，不构成投资建议"
    cache_txt = f" · 缓存于 {cached_hhmm}（非盘中实时）" if cached_hhmm else ""
    line = f"多级别联立(日线/30分/5分) · {mkt}({cur}) · {t_str}{cache_txt} · {disclaimer}"
    return {"tag": "markdown", "content": f"<font color='grey'>{line}</font>"}, line


# ---------------- A1：股票分析主卡片 (Q / B 创建&日报 / B 盘中) ----------------
def build_analysis_card(
    payload: dict,
    *,
    header_md: str = "",
    template: str | None = None,
    cached_hhmm: str | None = None,
) -> dict:
    report = payload.get("report") or {}
    b = report.get("basic") or {}
    c = report.get("chan_signal") or {}
    o = report.get("operation") or {}
    a = report.get("auxiliary") or {}

    title = payload.get("title") or f"📊 {b.get('name', '')} {b.get('code', '')}".strip()
    direction = o.get("direction") or "观望"
    tpl = template or payload.get("template") or {"买入": "red", "卖出": "green"}.get(direction, "blue")

    price = b.get("price")
    pct = b.get("change_pct")
    currency = b.get("currency") or "CNY"
    vol = a.get("volume_status") or "未知"
    sup = a.get("support")
    res = a.get("resistance")
    bsp = c.get("buy_sell_point") or "无信号"

    dir_icon = {"买入": "🔴 买入信号", "卖出": "🟢 卖出信号"}.get(direction, "🟠 观望等待")
    pct_plain = f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else ""
    price_plain = f"{price:g} {currency}{pct_plain}" if isinstance(price, (int, float)) else "—"
    subtitle = f"{dir_icon} · 现价 {price_plain} · 日线{bsp}"

    elements: list[dict] = []
    flat_parts: list[str] = []

    if header_md:
        banner_bg = "red-50" if tpl == "red" else ("orange-50" if tpl == "orange" else "blue-50")
        elements.append(banner_box(header_md, bg=banner_bg))
        flat_parts.append(header_md)

    # 顶栏 KPI 色块: 观望态(无入场区间)渲染 3 列[现价/支撑/压力], 彻底省去无内容的入场/止损/目标行
    p_col, p_bg = pct_theme(pct)
    price_val_md = f"**<font color='{p_col}'>{price:g} {currency}</font>**" if isinstance(price, (int, float)) else "**—**"
    price_sub_md = f"{fmt_pct(pct)} · 量能{vol}"

    ez = o.get("entry_zone")
    stop = o.get("stop_loss")
    target = o.get("target_price")
    rr = o.get("rr_ratio")
    pos_adv = o.get("position_advice")

    if ez or direction in ("买入", "卖出"):
        ez_str = f"{ez[0]:g}-{ez[1]:g}" if isinstance(ez, (list, tuple)) and len(ez) == 2 else direction
        act_col = "red" if direction == "买入" else "green"
        act_bg = "red-50" if direction == "买入" else "green-50"
        cols = [
            kpi_col(f"最新价 ({currency})", price_val_md, price_sub_md, bg=p_bg),
            kpi_col("建议介入区间" if ez else "操作方向",
                    f"**<font color='{act_col}'>{ez_str}</font>**",
                    f"<font color='grey'>{pos_adv or bsp}</font>", bg=act_bg),
            kpi_col("🎯 目标位",
                    f"**<font color='red'>{target:g}</font>**" if isinstance(target, (int, float)) else "**—**",
                    f"<font color='grey'>盈亏比 {rr or '—'} · 压力 {res if res is not None else '—'}</font>",
                    bg="red-50" if direction == "买入" else "grey-50"),
            kpi_col("🛑 止损位",
                    f"**<font color='orange'>{stop:g}</font>**" if isinstance(stop, (int, float)) else "**—**",
                    f"<font color='grey'>支撑 {sup if sup is not None else '—'} · 破位风控</font>",
                    bg="orange-50"),
        ]
        elements.append(kpi_row(cols, flex_mode="flow"))
        flat_parts.append(
            f"现价 {price_plain} ｜ 方向：{direction} ｜ 入场：{ez_str} ｜ 止损：{stop} ｜ 目标：{target} ｜ 盈亏比：{rr}"
        )
    else:
        sup_sub = "跌破则下看前低"
        if isinstance(price, (int, float)) and isinstance(sup, (int, float)) and price > 0:
            sup_sub = f"距现价 {(sup - price) / price * 100:+.2f}% · 关键防守位"
        res_sub = "突破则打开空间"
        if isinstance(price, (int, float)) and isinstance(res, (int, float)) and price > 0:
            res_sub = f"距现价 {(res - price) / price * 100:+.2f}% · 关键阻力位"

        cols = [
            kpi_col(f"最新价 ({currency})", price_val_md, price_sub_md, bg=p_bg),
            kpi_col("🟢 下方支撑位",
                    f"**<font color='turquoise'>{sup:g}</font>**" if isinstance(sup, (int, float)) else "**—**",
                    f"<font color='grey'>{sup_sub}</font>", bg="turquoise-50"),
            kpi_col("🟠 上方压力位",
                    f"**<font color='orange'>{res:g}</font>**" if isinstance(res, (int, float)) else "**—**",
                    f"<font color='grey'>{res_sub}</font>", bg="orange-50"),
        ]
        elements.append(kpi_row(cols, flex_mode="trisect"))
        flat_parts.append(f"现价 {price_plain} ｜ 方向：观望 ｜ 支撑：{sup} ｜ 压力：{res} ｜ 量能：{vol}")

    chan_rows, chan_summary = _chan_structure_rows(report)
    elements.extend(chan_rows)
    flat_parts.append(chan_summary)

    elements.append({"tag": "hr"})
    disclaimer = report.get("disclaimer") or "⚠️ 仅供参考，不构成投资建议"
    llm_md = (payload.get("markdown") or "").strip()
    body_md = llm_md[:-len(disclaimer)].rstrip() if llm_md.endswith(disclaimer) else llm_md
    elements.append({"tag": "markdown", "content": body_md or llm_md})
    flat_parts.append( body_md or llm_md)

    elements.append({"tag": "hr"})
    footer_el, footer_txt = _footer_element(report, cached_hhmm=cached_hhmm)
    elements.append(footer_el)
    flat_parts.append(footer_txt)

    raw_card = schema2_card(title, subtitle, tpl, elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": tpl,
            "markdown": "\n\n".join(p for p in flat_parts if p),
            "raw_card": raw_card,
        }
    }


# ---------------- B1：持仓跟踪卡片 (S 创建/更新 / S 日报) ----------------
def build_holding_card(
    payload: dict,
    *,
    banner_title: str,
    price: float,
    cost: float | None,
    qty: int | None,
    stop: float,
    stop_source: str,
    target: float,
    notes: list[str] | None = None,
    sell_signal: str | None = None,
    code: str | None = None,
) -> dict:
    report = payload.get("report") or {}
    b = report.get("basic") or {}
    raw_code = code or payload.get("code") or "000001"
    currency = b.get("currency") or "CNY"
    pct = b.get("change_pct")
    title = payload.get("title") or f"📊 {b.get('name', '')} {b.get('code', '')}".strip()

    dist = holding.dist_to_stop_pct(price, stop)
    target_dist = round((target - price) / price * 100, 2) if price else 0.0
    d_pnl = holding.pnl(cost, qty, price)
    pnl_pct = d_pnl["pnl_pct"]
    pnl_amt = d_pnl["pnl_amount"]
    mv = d_pnl["market_value"]

    if sell_signal:
        tpl = "green"
    elif dist <= 0:
        tpl = "orange"
    elif pnl_pct is not None and pnl_pct > 0:
        tpl = "red"
    else:
        tpl = payload.get("template") or "blue"

    cost_sub = f"成本 {cost:g} · 浮盈 {pnl_pct:+.2f}% · " if cost and pnl_pct is not None else f"现价 {price:g} · "
    subtitle = f"💼 持仓跟踪 · {cost_sub}止损 {stop:g} (距现价 {dist:+.2f}%)"

    elements: list[dict] = []
    # 生成兼容单测与 CLI 摘要的确定性头部文本
    note_str = "；".join(notes or [])
    rpt_time = daily_report_time_str(raw_code)
    legacy_header = holding.holding_header_md(
        banner_title, price, cost, qty, stop, stop_source, target, note_str
    )
    legacy_header += f"\n- 监控：盘中每 3 分钟比价，触及止损立即提醒；每日 {rpt_time} 重算止损（只上移不下移）并推送日报"
    flat_parts: list[str] = [legacy_header]

    # 顶部状态横幅
    banner_lines = [f"**{banner_title}**"]
    for n in (notes or []):
        banner_lines.append(f"• {n}")
    if dist <= 0:
        banner_lines.append("⚠️ <font color='red'>**当前价已低于止损位，将触发止损提醒**</font>")
    elements.append(banner_box("\n".join(banner_lines), bg="orange-50" if dist <= 0 else "blue-50"))

    # 第 1 排 KPI: 持仓账本(仅在提供成本价时渲染; 未填成本直接隐藏本排, 不写废话行)
    if cost:
        pnl_col, pnl_bg = pct_theme(pnl_pct)
        qty_txt = f"持仓 {qty} 股" if qty else "未填股数"
        mv_txt = f" · 市值 **{mv:,.2f}**" if mv is not None else ""
        amt_txt = f"（{fmt_amt(pnl_amt)}）" if pnl_amt is not None else ""
        row1_cols = [
            kpi_col(f"💼 成本 → 现价 ({currency})",
                    f"**<font color='blue'>{cost:g} → {price:g}</font>**",
                    f"<font color='grey'>{qty_txt}{mv_txt}</font>", bg="blue-50"),
            kpi_col("💰 浮动盈亏",
                    f"{fmt_pct(pnl_pct)}{amt_txt}",
                    f"<font color='grey'>今日涨跌 {fmt_pct(pct, bold=False)}</font>", bg=pnl_bg),
        ]
        elements.append(kpi_row(row1_cols, flex_mode="bisect"))

    # 第 2 排 KPI: 风控锚点 3 列
    stop_col = "red" if dist <= 0 else "orange"
    stop_bg = "red-50" if dist <= 0 else "orange-50"
    stop_sub = (f"<font color='red'>**已跌破 ({dist:+.2f}%)**</font>"
                if dist <= 0 else f"<font color='grey'>{stop_source} · 安全垫 {dist:+.2f}%</font>")

    if sell_signal:
        status_val = f"**<font color='green'>⚠️ {sell_signal}</font>**"
    elif any("止损上移" in x for x in (notes or [])):
        status_val = "**<font color='violet'>🔒 止损已上移</font>**"
    elif not cost:
        status_val = f"**<font color='violet'>现价 {price:g}</font>**"
    else:
        status_val = "**<font color='violet'>🟢 正常监控</font>**"

    row2_cols = [
        kpi_col("🛑 止损位", f"**<font color='{stop_col}'>{stop:g}</font>**", stop_sub, bg=stop_bg),
        kpi_col("🎯 目标位", f"**<font color='red'>{target:g}</font>**",
                f"<font color='grey'>距现价空间 {target_dist:+.2f}%</font>", bg="red-50"),
        kpi_col("📡 监控策略", status_val,
                f"<font color='grey'>盘中3m比价 · {rpt_time}只上移</font>", bg="violet-50"),
    ]
    elements.append(kpi_row(row2_cols, flex_mode="trisect"))

    chan_rows, chan_summary = _chan_structure_rows(report)
    elements.extend(chan_rows)
    flat_parts.append(chan_summary)

    elements.append({"tag": "hr"})
    disclaimer = report.get("disclaimer") or "⚠️ 仅供参考，不构成投资建议"
    llm_md = (payload.get("markdown") or "").strip()
    body_md = llm_md[:-len(disclaimer)].rstrip() if llm_md.endswith(disclaimer) else llm_md
    elements.append({"tag": "markdown", "content": body_md or llm_md})
    flat_parts.append(body_md or llm_md)

    elements.append({"tag": "hr"})
    footer_el, footer_txt = _footer_element(report)
    elements.append(footer_el)
    flat_parts.append(footer_txt)

    raw_card = schema2_card(title, subtitle, tpl, elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": tpl,
            "markdown": "\n\n".join(p for p in flat_parts if p),
            "raw_card": raw_card,
        }
    }


# ---------------- C1：任务列表卡片 (`L` 指令, 灰底多列表格) ----------------
def build_task_list_card(scope: str, items: list[dict]) -> dict:
    """items 每项包含:
    display, name, disp_code, task_type('B'|'S'), price, change_pct,
    buy_price, buy_quantity, pnl_pct, stop_loss, target_price, dist_to_stop,
    support, resistance, last_signal, urgent(bool), legacy_block(str)
    """
    n_b = sum(1 for x in items if x["task_type"] == "B")
    n_s = sum(1 for x in items if x["task_type"] == "S")
    title = f"📋 任务列表（{scope}，共 {len(items)} 个）"
    subtitle = f"买入跟踪(B) {n_b} 项 · 持仓跟踪(S) {n_s} 项 · ❗表示信号触发或临近止损"

    # 紧急项置顶(出现买卖点或距止损 <= 3%)
    sorted_items = sorted(items, key=lambda x: (0 if x.get("urgent") else 1))

    elements: list[dict] = [
        grey_row([
            ("**标的**", 2),
            ("**类型**", 1),
            ("**现价 / 涨跌**", 2),
            ("**盈亏 / 关键位**", 2),
            ("**风控 / 最近结论**", 2),
        ]),
        {"tag": "hr"},
    ]

    for it in sorted_items:
        prefix = "❗ " if it.get("urgent") else ""
        col_stock = f"{prefix}**{it['name']}**\n<font color='grey'>{it['disp_code']}</font>"
        col_type = "🔵 **买入(B)**" if it["task_type"] == "B" else "🟠 **持仓(S)**"

        px, pct = it.get("price"), it.get("change_pct")
        col_px = f"**{px:g}**\n{fmt_pct(pct)}" if px is not None else "<font color='grey'>—</font>"

        if it["task_type"] == "S":
            if it.get("buy_price"):
                qty_s = f"×{it['buy_quantity']}" if it.get("buy_quantity") else ""
                col_mid = f"成本 {it['buy_price']:g}{qty_s}\n浮盈 {fmt_pct(it.get('pnl_pct'))}"
            else:
                tgt = f"{it['target_price']:g}" if it.get("target_price") else "—"
                col_mid = f"目标 **{tgt}**\n<font color='grey'>未设成本</font>"

            stop = it.get("stop_loss")
            dist = it.get("dist_to_stop")
            if stop is not None and dist is not None:
                d_col = "red" if dist <= 3.0 else "grey"
                stop_line = f"🛑 止损 **{stop:g}** (<font color='{d_col}'>{dist:+.2f}%</font>)"
            elif stop is not None:
                stop_line = f"🛑 止损 **{stop:g}**"
            else:
                stop_line = "🛑 止损 —"
            col_right = f"{stop_line}\n<font color='grey'>{it['last_signal']}</font>"
        else:
            sup, res = it.get("support"), it.get("resistance")
            if sup is not None or res is not None:
                col_mid = f"支撑 **{sup if sup is not None else '—'}**\n压力 **{res if res is not None else '—'}**"
            else:
                col_mid = "<font color='grey'>待首次分析</font>"
            sig = it["last_signal"]
            sig_icon = "🔴" if "买点" in sig else ("🟢" if "卖点" in sig else "🟠")
            col_right = f"{sig_icon} {sig}"

        elements.append(grey_row([
            (col_stock, 2),
            (col_type, 1),
            (col_px, 2),
            (col_mid, 2),
            (col_right, 2),
        ]))

    elements.append({"tag": "hr"})
    elements.append({
        "tag": "markdown",
        "content": "<font color='grey'>发送 Q 代码 查看完整报告 · 发送 S 代码 @成本 数量 切换持仓 · 发送 C 代码 取消任务</font>",
    })

    raw_card = schema2_card(title, subtitle, "blue", elements)
    return {
        "card": {
            "title": title,
            "subtitle": subtitle,
            "template": "blue",
            "markdown": "\n\n".join(x["legacy_block"] for x in items),
            "raw_card": raw_card,
        }
    }


# ---------------- D1：盘中止损告警卡片 ----------------
def build_stop_alert_card(t, price: float, first: bool, disclaimer: str) -> dict:
    dist = holding.dist_to_stop_pct(price, t.stop_loss)
    disp = display_code(t.stock_code)
    title = f"🚨 止损触发 {t.display}" if first else f"⏰ 止损提醒（持续）{t.display}"
    subtitle = f"现价 {price:g} ≤ 止损位 {t.stop_loss:g}（低于止损位 {abs(dist):.2f}%）· 请关注持仓风控"
    tpl = "orange"

    cols = [
        kpi_col("🔴 当前现价",
                f"**<font color='red'>{price:g}</font>**",
                "<font color='red'>已触及止损预警线</font>", bg="red-50"),
        kpi_col("🛑 触发止损位",
                f"**<font color='orange'>{t.stop_loss:g}</font>**",
                f"<font color='red'>**低于止损位 {abs(dist):.2f}%**</font>", bg="orange-50"),
    ]

    flat_lines = [
        f"**现价 {price:g} ≤ 止损位 {t.stop_loss:g}**（低于止损位 {abs(dist):.2f}%）"
        if first else f"**仍低于止损位**：现价 {price:g}｜止损位 {t.stop_loss:g}（{abs(dist):.2f}%）"
    ]

    if t.buy_price:
        d = holding.pnl(t.buy_price, t.buy_quantity, price)
        _, p_bg = pct_theme(d["pnl_pct"])
        qty_txt = f" × {t.buy_quantity} 股" if t.buy_quantity else ""
        amt_txt = f"（{fmt_amt(d['pnl_amount'])}）" if d["pnl_amount"] is not None else ""
        cols.append(
            kpi_col(f"💼 成本 {t.buy_price:g}{qty_txt}",
                    f"{fmt_pct(d['pnl_pct'])}{amt_txt}",
                    f"<font color='grey'>目标位 {t.target_price:g}</font>" if t.target_price else "",
                    bg=p_bg)
        )
        row = f"- 成本：{t.buy_price:g}{qty_txt}｜浮盈：**{d['pnl_pct']:+.2f}%**"
        if d["pnl_amount"] is not None:
            row += f"（{d['pnl_amount']:+,.2f}）"
        flat_lines.append(row)
    elif t.target_price:
        cols.append(
            kpi_col("🎯 原定目标位", f"**<font color='blue'>{t.target_price:g}</font>**",
                    "<font color='grey'>未设置持仓成本</font>", bg="blue-50")
        )

    if t.target_price:
        flat_lines.append(f"- 目标位：{t.target_price:g}")

    guide_md = (
        "#### 💡 盘中破位应对参考\n"
        f"- **辨别真假破位**：观察未来 15~30 分钟分时走势与量能——若缩量下探后迅速拉回 **{t.stop_loss:g}** 上方，"
        "多为盘中波动下影线；若放量跌破且反抽无力，则意味着结构支撑失效。\n"
        "- **风控纪律原则**：止损位由日线缠论结构支撑与成本保护共同锚定，有效跌破代表原有多头结构走弱，建议按纪律控制回撤。\n"
        f"- **任务状态管理**：若价格仍低于止损位，系统将每 30 分钟提醒一次；若已线下处理可发送 `C {disp}` 取消跟踪，"
        f"或发送 `S {disp} @新成本` 重置止损位。"
    )
    flat_lines.append(guide_md)
    flat_lines.append(f"\n{disclaimer}")

    elements: list[dict] = [
        kpi_row(cols, flex_mode="trisect" if len(cols) == 3 else "bisect"),
        {"tag": "hr"},
        {"tag": "markdown", "content": guide_md},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"<font color='grey'>这是价格风控提醒，不是自动交易指令 · {disclaimer}</font>"},
    ]

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
