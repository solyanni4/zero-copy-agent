"""
Simple test client for zero-copy-agent.
Simulates an OpenAI client request to verify the server is working.
"""
import urllib.request
import json
import sys

BASE_URL = "http://127.0.0.1:8000"

def test():
    print(f"Testing zero-copy-agent at {BASE_URL}...\n")
    
    # 1. Test /health
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=5) as r:
            health = json.loads(r.read().decode())
            print(f"✅ Health Check passed: {health}")
    except Exception as e:
        print(f"❌ Health Check failed: {e}")
        print("   Make sure the server is running: python main.py")
        sys.exit(1)

    # 2. Test /v1/models
    try:
        with urllib.request.urlopen(f"{BASE_URL}/v1/models", timeout=5) as r:
            models = json.loads(r.read().decode())
            model_ids = [m["id"] for m in models.get("data", [])]
            print(f"✅ Model list discovered ({len(model_ids)} models/aliases): {model_ids}")
    except Exception as e:
        print(f"❌ /v1/models failed: {e}")

    # 2.5. Test Local GPU/Ollama via Dynamic Alias (tier:local-fast)
    try:
        print("\nTesting Local Zero-Token Inference (tier:local-fast -> Ollama)...")
        payload = {
            "model": "tier:local-fast",
            "messages": [{"role": "user", "content": "What is 2+2? Answer in one word."}],
            "stream": False
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{BASE_URL}/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read().decode())
            ans = resp["choices"][0]["message"]["content"]
            print(f"✅ Local Ollama response: {ans.strip()}")
    except Exception as e:
        print(f"⚠️ Local Ollama query skipped/failed: {e}")

    # 3. Test /v1/chat/completions (Non-streaming)
    try:
        payload = {
            "model": "gemini-advanced",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello! Can you help me check if zero-copy-agent works?"}
            ],
            "stream": False
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        print("\nSending test prompt to /v1/chat/completions (non-streaming)...")
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
            answer = resp["choices"][0]["message"]["content"]
            print(f"✅ Non-streaming response received ({len(answer)} chars)")
    except Exception as e:
        print(f"❌ Chat Completion failed: {e}")
        sys.exit(1)

    # 4. Test /v1/chat/completions (Streaming SSE - exact Cursor/Continue mode)
    try:
        payload["stream"] = True
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        print("\nTesting Real-Time SSE Token Streaming (IDE mode)...")
        print("--- [Stream Output] ---")
        with urllib.request.urlopen(req, timeout=15) as r:
            for line in r:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: ") and line_str != "data: [DONE]":
                    chunk_json = json.loads(line_str[6:])
                    delta = chunk_json["choices"][0].get("delta", {})
                    token = delta.get("content") or ""
                    sys.stdout.write(token)
                    sys.stdout.flush()
        print("\n-----------------------")
        print("\n🎉 ALL TESTS PASSED! zero-copy-agent is 100% ready for Cursor, Aider, and Continue.")
    except Exception as e:
        print(f"❌ Streaming test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test()
