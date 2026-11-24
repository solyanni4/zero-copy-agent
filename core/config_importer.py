"""
Universal Config Importer for zero-copy-agent.
Parses JSON, YAML, and ENV configurations (OpenCode AI SDK, standard provider dictionaries,
and shell environment variables) into uniform account profiles.
"""
import re
import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("zcao.importer")

try:
    import yaml
except ImportError:
    yaml = None


def mask_api_key(key: Optional[str]) -> str:
    """Masks secret API keys for safe UI previews and logging (e.g. sk-1...9a4b)."""
    if not key or not isinstance(key, str):
        return ""
    clean = key.strip()
    if len(clean) <= 8:
        return "***"
    return f"{clean[:4]}...{clean[-4:]}"


def _infer_protocol(provider_name: str, base_url: str, npm_package: str = "") -> str:
    """Infers protocol (anthropic, gemini, or openai) from metadata."""
    combined = f"{provider_name} {base_url} {npm_package}".lower()
    if "anthropic" in combined or "claude" in combined:
        return "anthropic"
    if "gemini" in combined or "generativelanguage" in combined or "google" in combined:
        return "gemini"
    return "openai"


def _parse_env_lines(raw_text: str) -> Dict[str, str]:
    """Extracts KEY=VALUE pairs from text (handling 'export KEY=VALUE' and quotes)."""
    env_vars: Dict[str, str] = {}
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k and v:
                env_vars[k] = v
    return env_vars


def _build_account_profile(
    alias: str,
    provider: str,
    protocol: str,
    base_url: str,
    api_key: str,
    models: List[str],
    proxy: str = "auto"
) -> Dict[str, Any]:
    """Helper to construct a standardized provider profile dict."""
    clean_models = []
    for m in models:
        if m and isinstance(m, str):
            mid = m.strip()
            if mid and mid not in clean_models:
                clean_models.append(mid)

    return {
        "alias": alias.strip() or f"{provider.capitalize()} API",
        "provider": provider.strip().lower() or "custom",
        "protocol": protocol.strip().lower(),
        "base_url": base_url.strip().rstrip("/"),
        "api_key": api_key.strip(),
        "masked_key": mask_api_key(api_key),
        "models": clean_models,
        "models_count": len(clean_models),
        "proxy": proxy or "auto"
    }


def parse_provider_config(raw_text: str) -> Dict[str, Any]:
    """
    Safely parses JSON, YAML, or ENV format into list of provider profiles.
    Never exposes raw unmasked keys in output logs.
    Returns: {
        "format_detected": str,
        "accounts": List[Dict[str, Any]],
        "errors": List[str]
    }
    """
    if not raw_text or not raw_text.strip():
        return {"format_detected": "Empty", "accounts": [], "errors": ["Пустая строка конфигурации"]}

    text = raw_text.strip()
    accounts: List[Dict[str, Any]] = []
    format_detected = "Unknown"
    errors: List[str] = []

    # 1. Attempt JSON / YAML parsing
    parsed_data = None
    try:
        parsed_data = json.loads(text)
    except Exception:
        if yaml:
            try:
                parsed_data = yaml.safe_load(text)
            except Exception:
                pass

    if isinstance(parsed_data, dict):
        # Case A: OpenCode / AI SDK format: { "provider": { "name": { ... } } } or { "providers": { ... } }
        prov_dict = parsed_data.get("provider") or parsed_data.get("providers")
        if isinstance(prov_dict, dict):
            format_detected = "OpenCode / AI SDK Config"
            for p_name, p_data in prov_dict.items():
                if not isinstance(p_data, dict):
                    continue
                opts = p_data.get("options") or {}
                if not isinstance(opts, dict):
                    opts = {}
                base_url = opts.get("baseURL") or opts.get("base_url") or p_data.get("baseURL") or p_data.get("base_url") or ""
                api_key = opts.get("apiKey") or opts.get("api_key") or p_data.get("apiKey") or p_data.get("api_key") or ""
                npm = p_data.get("npm") or opts.get("npm") or ""
                proto = _infer_protocol(p_name, base_url, npm)

                # Models could be dict { "model-id": { ... } } or list [ "model-id" ]
                m_raw = p_data.get("models") or opts.get("models") or []
                models = []
                if isinstance(m_raw, dict):
                    models = list(m_raw.keys())
                elif isinstance(m_raw, list):
                    models = [m if isinstance(m, str) else m.get("id", "") for m in m_raw if m]

                accounts.append(_build_account_profile(
                    alias=p_name,
                    provider=p_name,
                    protocol=proto,
                    base_url=base_url,
                    api_key=api_key,
                    models=models
                ))

        # Case B: Direct dictionary representing a single account: { "base_url": "...", "api_key": "...", ... }
        elif any(k in parsed_data for k in ("base_url", "baseURL", "api_key", "apiKey", "endpoint")):
            format_detected = "JSON / YAML Account"
            b_url = parsed_data.get("base_url") or parsed_data.get("baseURL") or parsed_data.get("url") or parsed_data.get("endpoint") or ""
            key = parsed_data.get("api_key") or parsed_data.get("apiKey") or parsed_data.get("token") or parsed_data.get("key") or ""
            p_name = parsed_data.get("provider") or parsed_data.get("name") or "custom"
            proto = parsed_data.get("protocol") or _infer_protocol(p_name, b_url)
            m_raw = parsed_data.get("models") or []
            models = list(m_raw.keys()) if isinstance(m_raw, dict) else [str(m) for m in m_raw]
            accounts.append(_build_account_profile(
                alias=parsed_data.get("alias") or p_name,
                provider=p_name,
                protocol=proto,
                base_url=b_url,
                api_key=key,
                models=models,
                proxy=parsed_data.get("proxy", "auto")
            ))

        # Case C: Dictionary mapping provider IDs to details: { "deepseek": { "api_key": "...", "base_url": "..." } }
        else:
            format_detected = "Provider Map Config"
            for p_name, p_data in parsed_data.items():
                if isinstance(p_data, dict):
                    b_url = p_data.get("base_url") or p_data.get("baseURL") or p_data.get("url") or ""
                    key = p_data.get("api_key") or p_data.get("apiKey") or p_data.get("token") or ""
                    proto = p_data.get("protocol") or _infer_protocol(p_name, b_url)
                    m_raw = p_data.get("models") or []
                    models = list(m_raw.keys()) if isinstance(m_raw, dict) else [str(m) for m in m_raw]
                    if key or b_url:
                        accounts.append(_build_account_profile(
                            alias=p_data.get("alias") or p_name,
                            provider=p_name,
                            protocol=proto,
                            base_url=b_url,
                            api_key=key,
                            models=models
                        ))

    elif isinstance(parsed_data, list):
        # Case D: List of account objects: [ { "base_url": "...", "api_key": "..." }, ... ]
        format_detected = "Accounts List Config"
        for item in parsed_data:
            if not isinstance(item, dict):
                continue
            b_url = item.get("base_url") or item.get("baseURL") or item.get("url") or item.get("endpoint") or ""
            key = item.get("api_key") or item.get("apiKey") or item.get("token") or item.get("key") or ""
            p_name = item.get("provider") or item.get("name") or "custom"
            proto = item.get("protocol") or _infer_protocol(p_name, b_url)
            m_raw = item.get("models") or []
            models = list(m_raw.keys()) if isinstance(m_raw, dict) else [str(m) for m in m_raw]
            accounts.append(_build_account_profile(
                alias=item.get("alias") or p_name,
                provider=p_name,
                protocol=proto,
                base_url=b_url,
                api_key=key,
                models=models,
                proxy=item.get("proxy", "auto")
            ))

    # 2. If JSON/YAML parsing produced no accounts, inspect for Environment Variables format
    if not accounts:
        env = _parse_env_lines(text)
        if env:
            format_detected = "Environment Variables (.env)"
            # Anthropic
            ant_url = env.get("ANTHROPIC_BASE_URL") or env.get("ANTHROPIC_URL") or "https://api.anthropic.com/v1"
            ant_key = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_KEY")
            if ant_key or "ANTHROPIC_BASE_URL" in env:
                accounts.append(_build_account_profile(
                    alias="Anthropic (ENV)",
                    provider="anthropic",
                    protocol="anthropic",
                    base_url=ant_url,
                    api_key=ant_key or "",
                    models=["claude-3-7-sonnet", "claude-3-5-sonnet"]
                ))

            # OpenAI
            oai_url = env.get("OPENAI_BASE_URL") or env.get("OPENAI_URL") or "https://api.openai.com/v1"
            oai_key = env.get("OPENAI_API_KEY") or env.get("OPENAI_KEY")
            if oai_key or "OPENAI_BASE_URL" in env:
                accounts.append(_build_account_profile(
                    alias="OpenAI (ENV)",
                    provider="openai",
                    protocol="openai",
                    base_url=oai_url,
                    api_key=oai_key or "",
                    models=["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"]
                ))

            # Gemini / Google
            gem_url = env.get("GEMINI_BASE_URL") or "https://generativelanguage.googleapis.com/v1beta/openai"
            gem_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
            if gem_key:
                accounts.append(_build_account_profile(
                    alias="Google Gemini (ENV)",
                    provider="gemini",
                    protocol="gemini",
                    base_url=gem_url,
                    api_key=gem_key,
                    models=["gemini-2.5-flash", "gemini-2.5-pro"]
                ))

            # DeepSeek
            ds_url = env.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1"
            ds_key = env.get("DEEPSEEK_API_KEY")
            if ds_key:
                accounts.append(_build_account_profile(
                    alias="DeepSeek (ENV)",
                    provider="deepseek",
                    protocol="openai",
                    base_url=ds_url,
                    api_key=ds_key,
                    models=["deepseek-chat", "deepseek-reasoner"]
                ))

            # OpenRouter
            or_url = env.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
            or_key = env.get("OPENROUTER_API_KEY")
            if or_key:
                accounts.append(_build_account_profile(
                    alias="OpenRouter (ENV)",
                    provider="openrouter",
                    protocol="openai",
                    base_url=or_url,
                    api_key=or_key,
                    models=["anthropic/claude-3.7-sonnet", "deepseek/deepseek-r1"]
                ))

    return {
        "format_detected": format_detected,
        "accounts": accounts,
        "errors": errors
    }
