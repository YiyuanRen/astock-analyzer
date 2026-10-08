"""单级别缠论分析: 封装 chan.py, 输入 OHLCV DataFrame, 输出结构化结果字典。

注意: 导入 app.engine 包时已把 vendor/chan.py 加入 sys.path。
chan.py 至少需要约 30 根K线才能形成有效分析; 不足时返回 {"error": ...}。
"""
from __future__ import annotations

import pandas as pd

# chan.py (vendored) —— 路径由 app/engine/__init__.py 注入
from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, BI_DIR, DATA_FIELD, DATA_SRC, KL_TYPE
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit

from app.config import get_config

# 产品级别名 -> chan.py KL_TYPE
LEVEL_MAP = {
    "daily": KL_TYPE.K_DAY,
    "30m": KL_TYPE.K_30M,
    "5m": KL_TYPE.K_5M,
}
LEVEL_CN = {"daily": "日线", "30m": "30分钟", "5m": "5分钟"}

# BSP 类型码 -> 一/二/三
_BSP_LEVEL = {"1": "一", "1p": "一", "2": "二", "2s": "二", "3a": "三", "3b": "三"}


def _df_to_klus(df: pd.DataFrame) -> list[CKLine_Unit]:
    klus = []
    for _, row in df.iterrows():
        d = str(row["date"])
        y, m, day = (int(x) for x in d[:10].split("-"))
        hour = minute = 0
        if len(d) > 10:
            hh, mm = d[11:16].split(":")
            hour, minute = int(hh), int(mm)
        item = {
            DATA_FIELD.FIELD_TIME: CTime(y, m, day, hour, minute),
            DATA_FIELD.FIELD_OPEN: float(row["open"]),
            DATA_FIELD.FIELD_HIGH: float(row["high"]),
            DATA_FIELD.FIELD_LOW: float(row["low"]),
            DATA_FIELD.FIELD_CLOSE: float(row["close"]),
            DATA_FIELD.FIELD_VOLUME: float(row["volume"]),
        }
        klus.append(CKLine_Unit(item))
    return klus


def _bsp_text(bsp) -> str:
    """CBS_Point -> '三类买点' 之类的中文。"""
    codes = [t.value for t in bsp.type]
    # 取优先级最高(数字最小)的类型作为主类型
    main = sorted(codes, key=lambda c: (c[0], c))[0]
    level = _BSP_LEVEL.get(main, "?")
    side = "买" if bsp.is_buy else "卖"
    return f"{level}类{side}点"


class ChanAnalyzer:
    def __init__(self, config: dict | None = None):
        app_cfg = get_config()
        self.min_klines = int(app_cfg.chan.get("min_klines", 30))
        self._chan_conf_dict = config or {
            "trigger_step": True,     # 手动喂K线, 不走内置数据源
            "bi_strict": True,
            "macd_algo": "peak",
            "print_warning": False,
        }

    def analyze(self, df: pd.DataFrame, level: str = "daily") -> dict:
        if df is None or len(df) < self.min_klines:
            return {"error": "数据不足，建议观望", "level": level,
                    "level_cn": LEVEL_CN.get(level, level)}

        kl_type = LEVEL_MAP.get(level, KL_TYPE.K_DAY)
        chan = CChan(code="_", data_src=DATA_SRC.BAO_STOCK, lv_list=[kl_type],
                     config=CChanConfig(dict(self._chan_conf_dict)), autype=AUTYPE.QFQ)
        chan.trigger_load({kl_type: _df_to_klus(df)})
        kl = chan.kl_datas[kl_type]

        if len(kl.bi_list) == 0:
            return {"error": "数据不足，建议观望", "level": level,
                    "level_cn": LEVEL_CN.get(level, level)}

        last_close = float(df["close"].iloc[-1])
        result = {
            "level": level,
            "level_cn": LEVEL_CN.get(level, level),
            "last_close": last_close,
            "n_merged_kline": len(kl.lst),
            "n_bi": len(kl.bi_list),
            "n_seg": len(kl.seg_list),
            "n_zs": len(kl.zs_list),
        }

        # 当前笔方向
        last_bi = kl.bi_list[-1]
        result["bi_direction"] = "向上" if last_bi.dir == BI_DIR.UP else "向下"
        result["bi_is_sure"] = bool(last_bi.is_sure)
        try:
            result["bi_bars"] = last_bi.get_end_klu().idx - last_bi.get_begin_klu().idx + 1
        except Exception:
            result["bi_bars"] = None
        try:
            result["bi_high"] = round(float(last_bi._high()), 2)
            result["bi_low"] = round(float(last_bi._low()), 2)
        except Exception:
            result["bi_high"] = result["bi_low"] = None

        # 线段方向(趋势) + 线段高低(用于目标/止损锚点)
        if len(kl.seg_list) > 0:
            last_seg = kl.seg_list[-1]
            result["segment_direction"] = "上升" if last_seg.dir == BI_DIR.UP else "下降"
            try:
                result["seg_high"] = round(float(last_seg._high()), 2)
                result["seg_low"] = round(float(last_seg._low()), 2)
            except Exception:
                result["seg_high"] = result["seg_low"] = None
        else:
            result["segment_direction"] = "震荡"
            result["seg_high"] = result["seg_low"] = None

        # 中枢 + 价格相对位置
        if len(kl.zs_list) > 0:
            zs = kl.zs_list[-1]
            zlow, zhigh = float(zs.low), float(zs.high)
            result["zhongshu_range"] = (round(zlow, 2), round(zhigh, 2))
            if last_close > zhigh:
                result["price_vs_zhongshu"] = "上方"
            elif last_close < zlow:
                result["price_vs_zhongshu"] = "下方"
            else:
                result["price_vs_zhongshu"] = "内部"
        else:
            result["zhongshu_range"] = None
            result["price_vs_zhongshu"] = None

        # 买卖点: 取最近一个, 判断是否"当前有效"(位于最后一笔)
        bsps = kl.bs_point_lst.getSortedBspList()
        if bsps:
            latest = bsps[-1]
            result["latest_bsp"] = {
                "text": _bsp_text(latest),
                "is_buy": bool(latest.is_buy),
                "types": [t.value for t in latest.type],
                "bi_idx": latest.bi.idx,
                "price": round(float(latest.bi.get_end_val()), 2),
            }
            # 是否为当前信号: 买卖点所在笔为最后一笔(或倒数第二笔且未完成)
            is_current = latest.bi.idx >= (len(kl.bi_list) - 1)
            result["buy_sell_point"] = _bsp_text(latest) if is_current else "无信号"
            # 背驰: 一类买卖点(含1p)本质由背驰定义
            main_types = [t.value for t in latest.type]
            result["macd_divergence"] = is_current and any(t in ("1", "1p") for t in main_types)
        else:
            result["latest_bsp"] = None
            result["buy_sell_point"] = "无信号"
            result["macd_divergence"] = False

        return result
