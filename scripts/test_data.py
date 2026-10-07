"""AKShare 数据层测试: 拉取平安银行(000001) 三周期 + 实时价。

运行: .venv\\Scripts\\python.exe scripts\\test_data.py
"""
import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.data import fetcher  # noqa: E402

CODE = "000001"


async def main():
    print(f"=== 并发拉取 {CODE} 三周期 ===")
    data = await fetcher.fetch_all_levels(CODE)
    for level, df in data.items():
        print(f"\n--- {level}: {len(df)} 根K线 ---")
        print(df.head(3).to_string(index=False))
        print("...")
        print(df.tail(2).to_string(index=False))

    print("\n=== 实时价 ===")
    price = await asyncio.to_thread(fetcher.fetch_realtime_price, CODE)
    print(f"{CODE} 最新价: {price}")

    ok = all(len(df) > 0 for df in data.values())
    print(f"\n数据层测试: {'✅ 通过' if ok else '❌ 失败'}")


if __name__ == "__main__":
    asyncio.run(main())
