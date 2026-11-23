"""
API Presets and Universal OpenAI-Compatible Provider Gateways for zero-copy-agent.
Provides built-in configurations for DeepSeek, Qwen/DashScope, xAI Grok,
OpenRouter, Groq, OpenAI, Anthropic, Gemini, and Custom OpenAI-Compatible endpoints.
"""
import logging
from typing import Dict, List, Optional, Any
import httpx

logger = logging.getLogger("zcao.providers")

API_PRESETS: Dict[str, Dict[str, Any]] = {
    "deepseek": {
        "id": "deepseek",
        "name": "DeepSeek API",
        "base_url": "https://api.deepseek.com/v1",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
        "description": "Официальный API DeepSeek V3 и DeepSeek R1 (DeepThink)",
        "auth_type": "Bearer",
        "doc_url": "https://platform.deepseek.com"
    },
    "qwen": {
        "id": "qwen",
        "name": "Qwen / DashScope (Alibaba)",
        "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "default_models": ["qwen-max", "qwen-plus", "qwen-turbo", "qwen-coder-turbo"],
        "description": "Alibaba Cloud DashScope (OpenAI-совместимый режим)",
        "auth_type": "Bearer",
        "doc_url": "https://help.aliyun.com/dashscope"
    },
    "grok": {
        "id": "grok",
        "name": "xAI Grok API",
        "base_url": "https://api.x.ai/v1",
        "default_models": ["grok-3", "grok-3-mini", "grok-2-1212", "grok-beta"],
        "description": "Официальный API xAI Grok 3 и Grok 2",
        "auth_type": "Bearer",
        "doc_url": "https://docs.x.ai"
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_models": ["deepseek/deepseek-r1", "anthropic/claude-3.7-sonnet", "openai/gpt-4o"],
        "description": "Единый агрегатор всех мировых LLM (OpenRouter Gateway)",
        "auth_type": "Bearer",
        "doc_url": "https://openrouter.ai"
    },
    "groq": {
        "id": "groq",
        "name": "Groq LPU Fast Inference",
        "base_url": "https://api.groq.com/openai/v1",
        "default_models": ["llama-3.3-70b-versatile", "deepseek-r1-distill-llama-70b", "mixtral-8x7b-32768"],
        "description": "Сверхбыстрый LPU-инференс для открытых моделей",
        "auth_type": "Bearer",
        "doc_url": "https://console.groq.com"
    },
    "gemini": {
        "id": "gemini",
        "name": "Google Gemini API",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_models": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "description": "Google AI Studio / Vertex AI OpenAI-совместимый эндпоинт",
        "auth_type": "Bearer",
        "doc_url": "https://aistudio.google.com"
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI API",
        "base_url": "https://api.openai.com/v1",
        "default_models": ["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"],
        "description": "Официальный API OpenAI ChatGPT и reasoning моделей",
        "auth_type": "Bearer",
        "doc_url": "https://platform.openai.com"
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic Claude API",
        "base_url": "https://api.anthropic.com/v1",
        "default_models": ["claude-3-7-sonnet", "claude-3-5-sonnet", "claude-3-haiku"],
        "description": "Официальный Messages API Anthropic Claude",
        "auth_type": "x-api-key",
        "doc_url": "https://console.anthropic.com"
    },
    "custom": {
        "id": "custom",
        "name": "Custom OpenAI-Compatible",
        "base_url": "",
        "default_models": ["custom-model"],
        "description": "Любой сторонний сервер или локальный шлюз с протоколом OpenAI /v1",
        "auth_type": "Bearer",
        "doc_url": ""
    }
}


def get_preset(provider_id: str) -> Optional[Dict[str, Any]]:
    """Returns preset definition for provider ID or None if not found."""
    return API_PRESETS.get(provider_id.lower())


def list_presets() -> List[Dict[str, Any]]:
    """Returns list of all available API presets."""
    return list(API_PRESETS.values())


NON_TEXT_PATTERNS = [
    "tts", "image", "embedding", "lyria", "aqa", "audio", "live", "robotics", "computer-use", "imagen", "dall-e", "whisper"
]


def clean_and_filter_model_id(raw_id: str) -> Optional[str]:
    """
    Cleans model identifier and filters out non-text/specialized multi-modal models.
    Strips 'models/' prefix if present (e.g. from Google /v1beta/openai/models).
    """
    if not raw_id or not isinstance(raw_id, str):
        return None
    mid = raw_id.strip()
    if mid.startswith("models/"):
        mid = mid[len("models/"):]
    low = mid.lower()
    for pat in NON_TEXT_PATTERNS:
        if pat in low:
            return None
    return mid


async def discover_remote_models(base_url: str, api_key: str, timeout_seconds: float = 8.0) -> List[str]:
    """
    Attempts to discover active models from an OpenAI-compatible /v1/models endpoint.
    Returns filtered list of text model IDs (without 'models/' prefix, excluding non-text models)
    or empty list if discovery endpoint is unsupported.
    """
    if not base_url:
        return []

    clean_base = base_url.rstrip("/")
    if not clean_base.startswith("http://") and not clean_base.startswith("https://"):
        clean_base = f"http://{clean_base}"
    clean_base = clean_base.replace("://0.0.0.0", "://127.0.0.1")
    models_url = f"{clean_base}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    is_local = any(h in clean_base for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=not is_local) as client:
            resp = await client.get(models_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("data", [])
                result = []
                for m in raw_models:
                    raw_id = m.get("id") if isinstance(m, dict) else (m if isinstance(m, str) else None)
                    clean_id = clean_and_filter_model_id(raw_id)
                    if clean_id:
                        result.append(clean_id)
                deduped = sorted(list(set(result)))
                logger.info(f"[Providers] Discovered {len(deduped)} models from {clean_base}")
                return deduped
            else:
                logger.debug(f"[Providers] /models returned {resp.status_code} for {clean_base}")
    except Exception as e:
        logger.debug(f"[Providers] Model discovery failed for {clean_base}: {e}")

    return []


def sort_accounts_by_auth_priority(accounts: List[Any]) -> List[Any]:
    """
    Sorts candidate accounts so API keys (auth_type == 'api_key') strictly precede
    browser web-sessions (auth_type == 'browser_session') and other accounts.
    """
    def _rank(acc):
        auth_type = getattr(acc, "auth_type", "api_key")
        if auth_type == "api_key":
            return 0
        elif auth_type == "local":
            return 1
        elif auth_type == "browser_session":
            return 2
        return 3
    return sorted(accounts, key=_rank)

