"""内置轻量只读 HTTP 服务(提供早报 HTML、实操账户看板 /portfolio、回测报告 /backtests/<id>)。

路由:
- GET /                         -> 历史早报归档目录页
- GET /briefings                -> 历史早报归档目录页
- GET /briefings/latest         -> 最新一期早报 HTML 页面
- GET /briefings/YYYY-MM-DD     -> 指定日期的早报 HTML 页面(磁盘+SQLite 双重持久化恢复)
- GET /portfolio                -> 10万本金模拟实操账户与交易流水可视化看板
- GET /backtests/<id>           -> 缠论无未来函数回测 HTML 报告
- GET /healthz                  -> 健康检查 {"status": "ok"}
"""
from __future__ import annotations

import asyncio
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.logging_utils import get_logger
from app.models import briefing_repo, trade_repo
from app.models.database import get_conn
from app.services import backtest_service, portfolio_service
from app.services.briefing_html import render_archive_html

logger = get_logger("web.briefing_server")

_DATE_ROUTE = re.compile(r"^/briefings/(20\d{2}-\d{2}-\d{2})(?:\.html)?/?$")
_BT_ROUTE = re.compile(r"^/backtests/([A-Za-z0-9_.-]+?)(?:\.html)?/?$")


def _default_active_chat_id() -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT chat_id FROM positions WHERE chat_id != 'cli_chat' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return row["chat_id"]
        row2 = conn.execute(
            "SELECT chat_id FROM tasks WHERE chat_id != 'cli_chat' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row2:
            return row2["chat_id"]
    return "cli_chat"


class BriefingHTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        logger.info("HTTP %s - %s", self.address_string(), fmt % args)

    def _send_html(self, status: int, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed_u = urlparse(self.path)
        path = parsed_u.path
        qs = parse_qs(parsed_u.query)

        if path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path in ("/", "/briefings", "/briefings/"):
            items = briefing_repo.list_briefings(limit=90)
            self._send_html(200, render_archive_html(items))
            return

        if path in ("/briefings/latest", "/briefings/latest/"):
            latest = briefing_repo.get_latest_briefing()
            if not latest:
                self._send_html(404, "<h1>暂无已生成的市场早报</h1><p><a href='/briefings'>返回归档</a></p>")
                return
            self._send_html(200, latest.html_content)
            return

        if path in ("/portfolio", "/portfolio/"):
            chat_id = (qs.get("chat_id") or [None])[0] or _default_active_chat_id()
            summary = asyncio.run(portfolio_service.get_portfolio_summary(chat_id))
            logs = trade_repo.list_trade_logs(chat_id, limit=50)
            self._send_html(200, portfolio_service.render_portfolio_html(summary, logs))
            return

        m_bt = _BT_ROUTE.match(path)
        if m_bt:
            rid = m_bt.group(1)
            bt_html = backtest_service.get_backtest_html(rid)
            if bt_html:
                self._send_html(200, bt_html)
                return
            self._send_html(404, f"<h2>未找到回测报告 {rid}</h2><p>请在飞书发送 <code>BT 代码</code> 生成。</p>")
            return

        m = _DATE_ROUTE.match(path)
        if m:
            r_date = m.group(1)
            html_doc = briefing_repo.get_or_restore_html(r_date)
            if html_doc:
                self._send_html(200, html_doc)
                return
            self._send_html(
                404,
                f"<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{r_date} 早报未找到</title></head><body style='font-family:sans-serif;padding:24px;'>"
                f"<h2>📅 {r_date} 早餐页面暂未生成</h2>"
                f"<p>您可以查看 <a href='/briefings'>🗂️ 往期早报历史归档</a>。</p></body></html>",
            )
            return

        self._send_html(404, "<h1>404 Not Found</h1><p><a href='/briefings'>进入市场早报归档</a> · <a href='/portfolio'>实操账户看板</a></p>")


def start_briefing_http_server(host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    """在后台守护线程启动 Web 服务并返回 server 实例。"""
    server = ThreadingHTTPServer((host, port), BriefingHTTPHandler)
    t = threading.Thread(target=server.serve_forever, name="briefing-http", daemon=True)
    t.start()
    logger.info("Web 服务已启动: http://%s:%d (/briefings | /portfolio | /backtests)", host, port)
    return server
