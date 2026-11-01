"""模拟交易实操账本数据仓库 (accounts / positions / trade_logs)。

核心业务规则:
1. 每个会话(chat_id)初始本金 100,000.00 元 RMB (INITIAL_CAPITAL = 100000.0)。
2. 港股(HK)折算人民币汇率默认 HKD_TO_RMB = 0.92; A股(CN)汇率 = 1.0。
3. 买入/加仓(BUY/ADD)扣减 cash_balance, 余额不足抛 InsufficientFundsError;
   卖出(SELL_PART/SELL_ALL)成交金额回流至 cash_balance。
4. A股(CN)严格实行 T+1 交收: 当日(trade_date)买入/加仓的股数当日锁定不可卖出,
   可卖股数 sellable_qty = current_qty - today_bought_qty; 港股(HK)实行 T+0(当日可买可卖)。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.data.market import decimals_of, display_code, market_of
from app.models.database import get_conn, now_str

INITIAL_CAPITAL = 100000.0
HKD_TO_RMB = 0.92


def fx_rate(market: str) -> float:
    return HKD_TO_RMB if market == "HK" else 1.0


class TradeError(Exception):
    """交易校验异常基类。"""


class InsufficientFundsError(TradeError):
    def __init__(self, cash_balance: float, required_rmb: float, max_qty: int):
        self.cash_balance = cash_balance
        self.required_rmb = required_rmb
        self.max_qty = max_qty
        super().__init__(
            f"可用现金不足：本次需 {required_rmb:,.2f} 元，当前可用现金 {cash_balance:,.2f} 元（最多可买 {max_qty} 股）"
        )


class NoOpenPositionError(TradeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(f"当前没有 {display_code(code)} 的持仓记录，无法执行卖出。")


class TPlusOneLockedError(TradeError):
    def __init__(
        self,
        code: str,
        current_qty: int,
        frozen_qty: int,
        sellable_qty: int = 0,
        requested_qty: int | None = None,
    ):
        self.code = code
        self.current_qty = current_qty
        self.frozen_qty = frozen_qty
        self.sellable_qty = sellable_qty
        self.requested_qty = requested_qty
        disp = display_code(code)
        if sellable_qty <= 0:
            msg = (
                f"⚠️ A股实行 **T+1** 交易制度：{disp} 当前持仓 {current_qty} 股全部为今日新买入，"
                "需下一交易日方可卖出（当前可卖 0 股）。"
            )
        else:
            msg = (
                f"⚠️ A股实行 **T+1** 交易制度：{disp} 当前总持仓 {current_qty} 股，其中今日新买入 "
                f"{frozen_qty} 股冻结不可卖，今日最多可卖底仓 **{sellable_qty}** 股（您请求卖出 {requested_qty} 股）。"
            )
        super().__init__(msg)


@dataclass
class Account:
    chat_id: str
    initial_capital: float
    cash_balance: float
    created_at: str
    updated_at: str


@dataclass
class Position:
    id: int
    chat_id: str
    stock_code: str
    stock_name: str | None
    market: str
    status: str
    avg_cost: float
    current_qty: int
    total_buy_qty: int
    realized_pnl_rmb: float
    entry_bsp: str | None
    opened_at: str
    closed_at: str | None
    updated_at: str

    @property
    def display(self) -> str:
        disp = display_code(self.stock_code)
        return f"{self.stock_name or disp} {disp}"


@dataclass
class TradeLog:
    id: int
    position_id: int
    chat_id: str
    stock_code: str
    stock_name: str | None
    market: str
    action: str
    price: float
    quantity: int
    amount_rmb: float
    realized_pnl_rmb: float | None
    pnl_pct: float | None
    chan_bsp: str | None
    trade_date: str
    traded_at: str


def get_or_create_account(chat_id: str) -> Account:
    ts = now_str()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO accounts(chat_id, initial_capital, cash_balance, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (chat_id, INITIAL_CAPITAL, INITIAL_CAPITAL, ts, ts),
        )
        row = conn.execute("SELECT * FROM accounts WHERE chat_id=?", (chat_id,)).fetchone()
    return Account(**dict(row))


def get_open_position(chat_id: str, stock_code: str) -> Position | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE chat_id=? AND stock_code=? AND status='open' ORDER BY id DESC LIMIT 1",
            (chat_id, stock_code),
        ).fetchone()
    return Position(**dict(row)) if row else None


def list_open_positions(chat_id: str | None = None) -> list[Position]:
    sql = "SELECT * FROM positions WHERE status='open'"
    args: list = []
    if chat_id:
        sql += " AND chat_id=?"
        args.append(chat_id)
    sql += " ORDER BY opened_at DESC, id DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Position(**dict(r)) for r in rows]


def list_closed_positions(chat_id: str | None = None, limit: int = 50) -> list[Position]:
    sql = "SELECT * FROM positions WHERE status='closed'"
    args: list = []
    if chat_id:
        sql += " AND chat_id=?"
        args.append(chat_id)
    sql += " ORDER BY closed_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [Position(**dict(r)) for r in rows]


def get_today_bought_qty(position_id: int, trade_date: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS q FROM trade_logs"
            " WHERE position_id=? AND trade_date=? AND action IN ('BUY', 'ADD')",
            (position_id, trade_date),
        ).fetchone()
    return int(row["q"] or 0) if row else 0


def get_sellable_qty(pos: Position, trade_date: str | None = None) -> tuple[int, int]:
    """返回 (今日可卖股数, 今日T+1冻结股数)。港股 T+0 无冻结。"""
    if pos.market == "HK":
        return pos.current_qty, 0
    t_date = trade_date or now_str()[:10]
    bought_today = get_today_bought_qty(pos.id, t_date)
    frozen = min(pos.current_qty, bought_today)
    return max(0, pos.current_qty - frozen), frozen


def record_buy(
    chat_id: str,
    stock_code: str,
    stock_name: str | None,
    price: float,
    quantity: int,
    *,
    chan_bsp: str | None = None,
    trade_date: str | None = None,
) -> dict:
    """记录模拟买入(新建仓 BUY 或加仓 ADD), 扣减可用现金。"""
    if price <= 0 or quantity <= 0:
        raise TradeError("买入价格与股数必须大于 0")
    mkt = market_of(stock_code)
    fx = fx_rate(mkt)
    dec = decimals_of(stock_code)
    amount_rmb = round(price * quantity * fx, 2)

    acct = get_or_create_account(chat_id)
    if amount_rmb > acct.cash_balance + 1e-6:
        max_qty = int(acct.cash_balance // (price * fx))
        raise InsufficientFundsError(acct.cash_balance, amount_rmb, max_qty)

    ts = now_str()
    t_date = trade_date or ts[:10]
    existing = get_open_position(chat_id, stock_code)

    with get_conn() as conn:
        new_cash = round(max(0.0, acct.cash_balance - amount_rmb), 2)
        conn.execute(
            "UPDATE accounts SET cash_balance=?, updated_at=? WHERE chat_id=?",
            (new_cash, ts, chat_id),
        )
        if existing:
            action = "ADD"
            new_qty = existing.current_qty + quantity
            new_total_buy = existing.total_buy_qty + quantity
            new_avg = round(
                (existing.avg_cost * existing.current_qty + price * quantity) / new_qty, dec + 1
            )
            conn.execute(
                "UPDATE positions SET stock_name=COALESCE(?, stock_name), avg_cost=?, current_qty=?,"
                " total_buy_qty=?, updated_at=? WHERE id=?",
                (stock_name, new_avg, new_qty, new_total_buy, ts, existing.id),
            )
            pos_id = existing.id
        else:
            action = "BUY"
            new_avg = round(price, dec)
            new_qty = quantity
            cur = conn.execute(
                "INSERT INTO positions(chat_id, stock_code, stock_name, market, status, avg_cost,"
                " current_qty, total_buy_qty, realized_pnl_rmb, entry_bsp, opened_at, updated_at)"
                " VALUES (?,?,?,?,'open',?,?,?,0.0,?,?,?)",
                (chat_id, stock_code, stock_name, mkt, new_avg, quantity, quantity, chan_bsp, ts, ts),
            )
            pos_id = cur.lastrowid

        conn.execute(
            "INSERT INTO trade_logs(position_id, chat_id, stock_code, stock_name, market, action,"
            " price, quantity, amount_rmb, realized_pnl_rmb, pnl_pct, chan_bsp, trade_date, traded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)",
            (pos_id, chat_id, stock_code, stock_name, mkt, action, price, quantity, amount_rmb, chan_bsp, t_date, ts),
        )

    pos = get_open_position(chat_id, stock_code)
    can_sell, frozen = get_sellable_qty(pos, t_date)
    return {
        "action": action,
        "position": pos,
        "price": price,
        "quantity": quantity,
        "amount_rmb": amount_rmb,
        "cash_balance": new_cash,
        "sellable_qty": can_sell,
        "frozen_qty": frozen,
    }


def record_sell(
    chat_id: str,
    stock_code: str,
    price: float,
    quantity: int | None = None,
    *,
    chan_bsp: str | None = None,
    trade_date: str | None = None,
) -> dict:
    """记录模拟卖出(部分减仓 SELL_PART 或全部清仓 SELL_ALL), 严格校验 A股 T+1。"""
    if price <= 0:
        raise TradeError("卖出价格必须大于 0")
    pos = get_open_position(chat_id, stock_code)
    if not pos:
        raise NoOpenPositionError(stock_code)

    ts = now_str()
    t_date = trade_date or ts[:10]
    can_sell, frozen = get_sellable_qty(pos, t_date)

    if can_sell <= 0:
        raise TPlusOneLockedError(stock_code, pos.current_qty, frozen, sellable_qty=0, requested_qty=quantity)

    if quantity is None:
        sell_qty = can_sell
    else:
        if quantity <= 0:
            raise TradeError("卖出股数必须大于 0")
        if quantity > pos.current_qty:
            sell_qty = pos.current_qty
        else:
            sell_qty = quantity
        if sell_qty > can_sell:
            raise TPlusOneLockedError(
                stock_code, pos.current_qty, frozen, sellable_qty=can_sell, requested_qty=quantity
            )

    fx = fx_rate(pos.market)
    amount_rmb = round(price * sell_qty * fx, 2)
    realized_pnl_rmb = round((price - pos.avg_cost) * sell_qty * fx, 2)
    pnl_pct = round((price / pos.avg_cost - 1) * 100, 2) if pos.avg_cost > 0 else 0.0

    remain_qty = pos.current_qty - sell_qty
    action = "SELL_ALL" if remain_qty == 0 else "SELL_PART"
    new_status = "closed" if remain_qty == 0 else "open"
    total_realized = round(pos.realized_pnl_rmb + realized_pnl_rmb, 2)

    acct = get_or_create_account(chat_id)
    new_cash = round(acct.cash_balance + amount_rmb, 2)

    with get_conn() as conn:
        conn.execute(
            "UPDATE accounts SET cash_balance=?, updated_at=? WHERE chat_id=?",
            (new_cash, ts, chat_id),
        )
        conn.execute(
            "UPDATE positions SET current_qty=?, realized_pnl_rmb=?, status=?,"
            " closed_at=?, updated_at=? WHERE id=?",
            (
                remain_qty,
                total_realized,
                new_status,
                (ts if remain_qty == 0 else None),
                ts,
                pos.id,
            ),
        )
        conn.execute(
            "INSERT INTO trade_logs(position_id, chat_id, stock_code, stock_name, market, action,"
            " price, quantity, amount_rmb, realized_pnl_rmb, pnl_pct, chan_bsp, trade_date, traded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pos.id, chat_id, stock_code, pos.stock_name, pos.market, action,
                price, sell_qty, amount_rmb, realized_pnl_rmb, pnl_pct, chan_bsp, t_date, ts,
            ),
        )

    return {
        "action": action,
        "stock_code": stock_code,
        "stock_name": pos.stock_name,
        "market": pos.market,
        "avg_cost": pos.avg_cost,
        "sell_price": price,
        "sell_qty": sell_qty,
        "remain_qty": remain_qty,
        "frozen_qty": min(remain_qty, frozen),
        "amount_rmb": amount_rmb,
        "realized_pnl_rmb": realized_pnl_rmb,
        "total_position_realized_rmb": total_realized,
        "pnl_pct": pnl_pct,
        "cash_balance": new_cash,
        "closed": remain_qty == 0,
    }


def list_trade_logs(chat_id: str | None = None, stock_code: str | None = None, limit: int = 30) -> list[TradeLog]:
    sql = "SELECT * FROM trade_logs WHERE 1=1"
    args: list = []
    if chat_id:
        sql += " AND chat_id=?"
        args.append(chat_id)
    if stock_code:
        sql += " AND stock_code=?"
        args.append(stock_code)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [TradeLog(**dict(r)) for r in rows]
