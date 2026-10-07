"""本地 CLI: 不经飞书直接测试指令全链路。

单次:   .venv\\Scripts\\python.exe scripts\\cli.py Q 000001
交互式: .venv\\Scripts\\python.exe scripts\\cli.py
"""
import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.commands.router import handle_async  # noqa: E402


async def run_once(text: str):
    print(f">>> {text}")
    reply = await handle_async(text, {})
    if isinstance(reply, dict) and reply.get("card"):
        c = reply["card"]
        print(f"[卡片] {c.get('title')}  (template={c.get('template')})")
        print(c.get("markdown"))
    else:
        print(reply)
    print()


async def main():
    if len(sys.argv) > 1:
        await run_once(" ".join(sys.argv[1:]))
        return
    print("A股缠论分析 CLI (输入指令，exit 退出)。例: Q 000001")
    while True:
        try:
            text = input("指令> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit", "q!"):
            break
        if text:
            await run_once(text)


if __name__ == "__main__":
    asyncio.run(main())
