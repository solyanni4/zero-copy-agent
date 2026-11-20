"""
⚡ Пример использования официального OpenAI SDK с zero-copy-agent.
"""
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="sk-zero-copy"
)

# 1. Потоковый ответ (Streaming)
print("--- [Streaming Response] ---")
stream = client.chat.completions.create(
    model="gemini-advanced",
    messages=[{"role": "user", "content": "Объясни в двух предложениях принцип Zero-Copy шлюза."}],
    stream=True
)

for chunk in stream:
    token = chunk.choices[0].delta.content or ""
    print(token, end="", flush=True)
print("\n")
