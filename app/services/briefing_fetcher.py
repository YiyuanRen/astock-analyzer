"""华尔街见闻「早餐FM-Radio」数据源抓取器。

数据源说明:
- 公开检索接口: https://api-one-wscn.awtmt.com/apiv1/search/article?query=早餐
- 公开文章详情: https://api-one-wscn.awtmt.com/apiv1/content/articles/{id}?extract=0
- 备选同源域名: https://api-one.wallstcn.com
- 无需登录或付费 Token, 通过 curl_cffi (浏览器 TLS 指纹) 访问并带多域名重试。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from curl_cffi import requests as cr

from app.logging_utils import get_logger

logger = get_logger("services.briefing_fetcher")

TZ = ZoneInfo("Asia/Shanghai")
SEARCH_PAGE_URL = "https://wallstreetcn.com/search?q=%E6%97%A9%E9%A4%90"

_API_HOSTS = [
    "https://api-one-wscn.awtmt.com",
    "https://api-one.wallstcn.com",
]


class BriefingSourceError(Exception):
    """数据源网络或接口异常。"""


class BriefingNotUpdatedError(Exception):
    """源站当日尚未发布新的早餐文章(如周日停更或 08:00 暂未出稿)。"""

    def __init__(self, target_date: str, latest_date: str | None = None, latest_title: str | None = None):
        self.target_date = target_date
        self.latest_date = latest_date
        self.latest_title = latest_title
        msg = f"{target_date} 华尔街见闻早餐暂未更新"
        if latest_date:
            msg += f"（源站最新一期为 {latest_date}）"
        super().__init__(msg)


@dataclass
class BriefingArticle:
    article_id: str
    report_date: str
    title: str
    uri: str
    published_at: str
    plain_text: str


def _clean_html_to_text(raw_html: str) -> str:
    """将文章 HTML 转为结构清晰的纯文本, 去除顶部商城推广噪音。"""
    if not raw_html:
        return ""
    s = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.S | re.I)
    s = re.sub(r"<style[^>]*>.*?</style>", "", s, flags=re.S | re.I)
    # 段落与换行标签转为换行符
    s = re.sub(r"</?(?:p|div|br|li|h[1-6]|tr|blockquote)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = []
    for line in s.splitlines():
        ln = re.sub(r"\s+", " ", line).strip()
        if not ln:
            continue
        # 过滤见闻历/大师课商城广告行
        if any(ad in ln for ad in ("见闻历预售", "点击图片立即订阅", "点击订阅", "连续第9年的财经")):
            continue
        lines.append(ln)
    return "\n".join(lines)


def _extract_date_from_item(item: dict) -> str:
    """从文章标题(如 '华尔街见闻早餐FM-Radio | 2026年10月17日')或 display_time 提取 YYYY-MM-DD。"""
    raw_title = re.sub(r"<[^>]+>", "", str(item.get("title") or ""))
    m = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", raw_title)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    ts = item.get("display_time")
    if ts:
        return datetime.fromtimestamp(int(ts), TZ).strftime("%Y-%m-%d")
    return ""


def _get_json(path: str, params: dict) -> dict:
    last_err = None
    for host in _API_HOSTS:
        url = f"{host}{path}"
        for attempt in range(2):
            try:
                r = cr.get(url, params=params, impersonate="chrome", timeout=15)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # noqa: BLE001
                last_err = e
                logger.warning("见闻接口请求失败 %s (第%d次): %s", url, attempt + 1, e)
    raise BriefingSourceError(f"华尔街见闻接口访问失败: {last_err}")


def search_breakfast_items(limit: int = 8) -> list[dict]:
    """搜索关键词『早餐』获取最近早餐文章列表。"""
    payload = _get_json("/apiv1/search/article", {"query": "早餐", "cursor": "", "limit": limit})
    items = ((payload.get("data") or {}).get("items")) or []
    out = []
    for it in items:
        clean_title = re.sub(r"<[^>]+>", "", str(it.get("title") or "")).strip()
        if "早餐" not in clean_title:
            continue
        r_date = _extract_date_from_item(it)
        ts = int(it.get("display_time") or 0)
        pub_str = datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M") if ts else r_date
        aid = str(it.get("id") or "")
        uri = it.get("uri") or f"https://wallstreetcn.com/articles/{aid}"
        out.append({
            "article_id": aid,
            "report_date": r_date,
            "title": clean_title,
            "uri": uri,
            "published_at": pub_str,
        })
    return out


def fetch_article_detail(article_id: str, fallback_meta: dict | None = None) -> BriefingArticle:
    """根据文章 ID 抓取早餐全文。"""
    payload = _get_json(f"/apiv1/content/articles/{article_id}", {"extract": 0})
    data = payload.get("data") or {}
    raw_html = data.get("content") or ""
    text = _clean_html_to_text(raw_html)
    if len(text) < 100:
        raise BriefingSourceError(f"文章 {article_id} 正文内容为空或过短(len={len(text)})")

    meta = fallback_meta or {}
    title = re.sub(r"<[^>]+>", "", str(data.get("title") or meta.get("title") or "")).strip()
    ts = int(data.get("display_time") or 0)
    pub_str = (
        datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M")
        if ts else (meta.get("published_at") or "")
    )
    r_date = meta.get("report_date") or _extract_date_from_item({"title": title, "display_time": ts})
    uri = meta.get("uri") or f"https://wallstreetcn.com/articles/{article_id}"
    return BriefingArticle(
        article_id=str(article_id),
        report_date=r_date,
        title=title,
        uri=uri,
        published_at=pub_str,
        plain_text=text,
    )


def fetch_breakfast_for_date(target_date: str | date | None = None) -> BriefingArticle:
    """抓取指定日期(默认今日)的华尔街见闻早餐全文; 若当日未更新则抛 BriefingNotUpdatedError。"""
    if target_date is None:
        t_str = datetime.now(TZ).strftime("%Y-%m-%d")
    elif isinstance(target_date, date):
        t_str = target_date.strftime("%Y-%m-%d")
    else:
        t_str = str(target_date).strip()

    items = search_breakfast_items(limit=10)
    if not items:
        raise BriefingSourceError("华尔街见闻『早餐』搜索结果为空")

    for it in items:
        if it["report_date"] == t_str:
            return fetch_article_detail(it["article_id"], fallback_meta=it)

    latest = items[0]
    raise BriefingNotUpdatedError(
        target_date=t_str,
        latest_date=latest.get("report_date"),
        latest_title=latest.get("title"),
    )
