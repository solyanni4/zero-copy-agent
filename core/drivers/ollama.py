"""
Ollama Local Inference Driver for zero-copy-agent.
Directly communicates with local Ollama runtime (http://127.0.0.1:11434).
Zero cloud tokens, ultra-low latency, and 100% offline-ready.
"""
import json
import logging
from typing import AsyncGenerator, List, Optional
import httpx
from core.drivers.base import BaseDriver

# Increased timeout for local models (e.g. 14b) so RTX 4070 has time to load into VRAM and generate code
DEFAULT_OLLAMA_TIMEOUT = httpx.Timeout(connect=10.0, read=240.0, write=30.0, pool=10.0)

class OllamaDriver(BaseDriver):
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        super().__init__(page=None)
        clean = (base_url or "http://127.0.0.1:11434").rstrip("/")
        if not clean.startswith("http://") and not clean.startswith("https://"):
            clean = f"http://{clean}"
        clean = clean.replace("://0.0.0.0", "://127.0.0.1")
        self.base_url = clean
        self.default_model = "qwen2.5:0.5b"
        self.is_local = any(h in self.base_url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))

    async def is_authenticated(self) -> bool:
        """Returns True if local Ollama daemon is active and responsive."""
        try:
            async with httpx.AsyncClient(timeout=2.0, trust_env=not self.is_local) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[str]:
        """Returns list of models installed locally in Ollama."""
        try:
            async with httpx.AsyncClient(timeout=3.0, trust_env=not self.is_local) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                if r.status_code == 200:
                    data = r.json()
                    models = [m.get("name") for m in data.get("models", []) if m.get("name")]
                    return models
        except Exception as e:
            logging.debug(f"[OLLAMA] list_models check failed: {e}")
        return []

    async def send_prompt(self, prompt: str, model: Optional[str] = None) -> str:
        """Non-streaming query to Ollama /api/generate."""
        chosen_model = model or self.default_model
        # Strip provider prefix if passed like 'ollama/qwen2.5:0.5b'
        if chosen_model.startswith("ollama/"):
            chosen_model = chosen_model[len("ollama/"):]

        payload = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": False
        }
        async with httpx.AsyncClient(timeout=DEFAULT_OLLAMA_TIMEOUT, trust_env=not self.is_local) as client:
            r = await client.post(f"{self.base_url}/api/generate", json=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("response", "").strip()

    async def send_prompt_stream(self, prompt: str, model: Optional[str] = None) -> AsyncGenerator[str, None]:
        """Streaming query to Ollama /api/generate (JSON lines stream)."""
        chosen_model = model or self.default_model
        if chosen_model.startswith("ollama/"):
            chosen_model = chosen_model[len("ollama/"):]

        payload = {
            "model": chosen_model,
            "prompt": prompt,
            "stream": True
        }
        async with httpx.AsyncClient(timeout=DEFAULT_OLLAMA_TIMEOUT, trust_env=not self.is_local) as client:
            async with client.stream("POST", f"{self.base_url}/api/generate", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("response", "")
                            if token:
                                yield token
                            if chunk.get("done", False):
                                break
                        except Exception:
                            continue
