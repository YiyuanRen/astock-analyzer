from datetime import date, datetime

from app.models.database import TZ
from app.services.analysis_service import AnalysisResult

# 2026-10-08(周四)为交易日; 10/1-10/7 休市
DATES = {date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8)}


def dt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def make_result(code="000001", name="平安银行", price=11.5, direction="观望", bsp="无信号",
                is_buy=True, bi_idx=40, seg_low=10.2, zs=(10.0, 10.6), seg_high=12.0, resistance=11.8,
                markdown="LLM正文"):
    daily = {"zhongshu_range": list(zs), "seg_low": seg_low, "seg_high": seg_high,
             "buy_sell_point": bsp,
             "latest_bsp": {"is_buy": is_buy, "price": 10.3, "bi_idx": bi_idx, "text": bsp}}
    payload = {
        "code": code, "name": name, "quote": {"price": price, "name": name}, "daily": daily,
        "ml": {"operation_direction": direction, "resistance": resistance},
        "report": {}, "markdown": markdown,
        "template": {"买入": "red", "卖出": "green"}.get(direction, "blue"),
        "title": f"📊 {name} {code}",
    }
    return AnalysisResult(code, name, payload, "2026-10-08T15:05:00+08:00", False)
