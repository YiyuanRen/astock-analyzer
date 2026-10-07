"""LLM 连通性测试: 分别调用 DeepSeek 与 MiMo, 验证 key 可用。

运行: .venv\\Scripts\\python.exe scripts\\test_llm.py
"""
import asyncio
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.llm.factory import get_llm_client  # noqa: E402

TESTS = [
    ("deepseek", "deepseek-chat"),
    ("mimo", "mimo-v2.6-flash"),
]

PROMPT = [
    {"role": "system", "content": "你是一个简洁的助手。"},
    {"role": "user", "content": "只回复两个字：你好"},
]


async def run_one(provider: str, model: str):
    print(f"\n=== 测试 {provider} / {model} ===")
    try:
        client = get_llm_client(provider, model)
    except Exception as e:
        print(f"  [配置错误] {e}")
        return False
    try:
        resp = await client.chat(PROMPT, max_tokens=50)
        print(f"  回复: {resp.content.strip()!r}")
        print(f"  模型: {resp.model}  tokens: in={resp.input_tokens} out={resp.output_tokens}")
        return True
    except Exception as e:
        print(f"  [调用失败] {type(e).__name__}: {e}")
        return False


async def main():
    results = {}
    for provider, model in TESTS:
        results[provider] = await run_one(provider, model)
    print("\n===== 结果汇总 =====")
    for provider, ok in results.items():
        print(f"  {provider}: {'✅ 可用' if ok else '❌ 不可用'}")


if __name__ == "__main__":
    asyncio.run(main())
