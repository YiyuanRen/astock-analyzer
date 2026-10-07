"""集成冒烟: 真实东方财富数据 -> chan.py 缠论计算。

验证数据层与缠论引擎的对接 (DataFrame -> CKLine_Unit -> CChan)。
运行: .venv\\Scripts\\python.exe scripts\\test_integration.py
"""
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "vendor", "chan.py"))

import pandas as pd  # noqa: E402

from app.data import fetcher  # noqa: E402
from Chan import CChan  # noqa: E402
from ChanConfig import CChanConfig  # noqa: E402
from Common.CEnum import AUTYPE, DATA_FIELD, DATA_SRC, KL_TYPE  # noqa: E402
from Common.CTime import CTime  # noqa: E402
from KLine.KLine_Unit import CKLine_Unit  # noqa: E402

CODE = "000001"


def df_to_klus(df: pd.DataFrame) -> list[CKLine_Unit]:
    klus = []
    for _, row in df.iterrows():
        d = str(row["date"])
        date_part = d[:10]
        y, m, day = (int(x) for x in date_part.split("-"))
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


def main():
    print(f"拉取 {CODE} 日线...")
    df = fetcher.fetch_daily(CODE, years=3)
    print(f"  {len(df)} 根日线, 区间 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

    config = CChanConfig({"trigger_step": True, "print_warning": False})
    chan = CChan(code=CODE, data_src=DATA_SRC.BAO_STOCK,
                 lv_list=[KL_TYPE.K_DAY], config=config, autype=AUTYPE.QFQ)
    chan.trigger_load({KL_TYPE.K_DAY: df_to_klus(df)})

    kl = chan.kl_datas[KL_TYPE.K_DAY]
    print(f"\n缠论计算结果 (日线):")
    print(f"  合并K线: {len(kl.lst)}")
    print(f"  笔: {len(kl.bi_list)}")
    print(f"  线段: {len(kl.seg_list)}")
    print(f"  中枢: {len(kl.zs_list)}")
    print(f"  买卖点: {len(kl.bs_point_lst)}")

    if len(kl.bi_list):
        lb = kl.bi_list[-1]
        print(f"  最后一笔: 方向={lb.dir} is_sure={lb.is_sure} "
              f"始值={lb.get_begin_val():.2f} 末值={lb.get_end_val():.2f}")
    if len(kl.zs_list):
        z = kl.zs_list[-1]
        print(f"  最后中枢: [{z.low:.2f}, {z.high:.2f}]")
    bsps = kl.bs_point_lst.getSortedBspList()
    for bsp in bsps[-3:]:
        types = ",".join(t.value for t in bsp.type)
        print(f"  买卖点: {'买' if bsp.is_buy else '卖'} 类型={types} @K线idx={bsp.klu.idx}")

    print("\n集成冒烟: ✅ 真实数据 -> 缠论计算 打通")


if __name__ == "__main__":
    main()
