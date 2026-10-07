"""chan.py 冒烟测试: 用合成K线验证缠论核心计算在当前 pandas/numpy 版本下可用。

运行: .venv\\Scripts\\python.exe scripts\\smoke_chan.py
"""
import math
import os
import sys

# 将 vendored chan.py 加入 sys.path
VENDOR = os.path.join(os.path.dirname(__file__), "..", "vendor", "chan.py")
sys.path.insert(0, os.path.abspath(VENDOR))

from Chan import CChan  # noqa: E402
from ChanConfig import CChanConfig  # noqa: E402
from Common.CEnum import AUTYPE, DATA_FIELD, DATA_SRC, KL_TYPE  # noqa: E402
from Common.CTime import CTime  # noqa: E402
from KLine.KLine_Unit import CKLine_Unit  # noqa: E402


def make_synthetic_klines(n=120):
    """构造一段有上涨/回调/震荡的合成日线, 足以形成笔/线段/中枢。"""
    klus = []
    price = 10.0
    for i in range(n):
        # 叠加趋势 + 正弦波动, 制造分形
        trend = 0.03 * i
        wave = 1.2 * math.sin(i / 3.0)
        close = 10.0 + trend + wave
        open_ = close - 0.15 * math.cos(i / 3.0)
        high = max(open_, close) + 0.2
        low = min(open_, close) - 0.2
        day = 1 + i
        year, month = 2024, 1
        # 简单日期滚动
        month += (day - 1) // 28
        d = (day - 1) % 28 + 1
        month = (month - 1) % 12 + 1
        t = CTime(year, month, d, 0, 0)
        item = {
            DATA_FIELD.FIELD_TIME: t,
            DATA_FIELD.FIELD_OPEN: float(open_),
            DATA_FIELD.FIELD_HIGH: float(high),
            DATA_FIELD.FIELD_LOW: float(low),
            DATA_FIELD.FIELD_CLOSE: float(close),
            DATA_FIELD.FIELD_VOLUME: float(10000 + i * 10),
        }
        klus.append(CKLine_Unit(item))
    return klus


def main():
    config = CChanConfig({
        "trigger_step": True,   # 不自动从数据源加载, 手动喂K线
        "bi_strict": True,
        "macd_algo": "peak",
        "print_warning": False,
    })
    chan = CChan(
        code="SMOKE",
        data_src=DATA_SRC.BAO_STOCK,  # 占位, trigger_step 下不会真正调用
        lv_list=[KL_TYPE.K_DAY],
        config=config,
        autype=AUTYPE.QFQ,
    )

    klus = make_synthetic_klines(120)
    chan.trigger_load({KL_TYPE.K_DAY: klus})

    kl_list = chan.kl_datas[KL_TYPE.K_DAY]
    n_merged = len(kl_list.lst)
    n_bi = len(kl_list.bi_list)
    n_seg = len(kl_list.seg_list)
    n_zs = len(kl_list.zs_list)
    n_bsp = len(kl_list.bs_point_lst.lst)

    print(f"合并K线数: {n_merged}")
    print(f"笔数(bi): {n_bi}")
    print(f"线段数(seg): {n_seg}")
    print(f"中枢数(zs): {n_zs}")
    print(f"买卖点数(bsp): {n_bsp}")

    if n_bi > 0:
        last_bi = kl_list.bi_list[-1]
        print(f"最后一笔方向: {last_bi.dir}, is_sure={last_bi.is_sure}")
    if n_zs > 0:
        last_zs = kl_list.zs_list[-1]
        print(f"最后中枢区间: low={last_zs.low:.2f} high={last_zs.high:.2f}")
    for bsp in kl_list.bs_point_lst.lst[-3:]:
        print(f"买卖点: is_buy={bsp.is_buy}, type={[t.value for t in bsp.type]}")

    assert n_bi > 0, "未计算出任何笔, 可能存在兼容性问题"
    print("\nchan.py 冒烟测试通过 ✅")


if __name__ == "__main__":
    main()
