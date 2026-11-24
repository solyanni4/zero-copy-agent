"""
Model Discovery and API Key Probing Service for zero-copy-agent.
Allows testing and discovering models from any OpenAI-compatible,
Anthropic-compatible, or Gemini API endpoint with proxy routing.
"""
import time
import logging
from typing import Dict, List, Optional, Any
import httpx

from core.providers import clean_and_filter_model_id

logger = logging.getLogger("zcao.discovery")


def resolve_http_proxy(proxy_setting: Optional[str] = "auto") -> Optional[str]:
    """Resolves effective proxy URL from account setting ('auto', 'direct', or custom)."""
    if not proxy_setting or proxy_setting.strip().lower() in ("direct", "none", ""):
        return None
    clean = proxy_setting.strip()
    if clean.lower() == "auto":
        try:
            from core.auth import detect_proxy
            return detect_proxy()
        except Exception:
            return None
    return clean


def normalize_base_url(raw_url: str, protocol: str = "openai") -> str:
    """
    Normalizes base_url: ensures scheme, trims trailing slashes,
    and strips resource endpoints (/models, /chat/completions, /messages).
    """
    if not raw_url or not raw_url.strip():
        if protocol == "anthropic":
            return "https://api.anthropic.com/v1"
        elif protocol == "gemini":
            return "https://generativelanguage.googleapis.com/v1beta/openai"
        return "https://api.openai.com/v1"

    url = raw_url.strip().rstrip("/")
    if "://" not in url:
        is_local = any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1"))
        url = f"http://{url}" if is_local else f"https://{url}"

    url = url.replace("://0.0.0.0", "://127.0.0.1")

    # Strip sub-endpoints if user entered full path
    for suffix in ("/chat/completions", "/messages", "/models"):
        if url.endswith(suffix):
            url = url[:-len(suffix)].rstrip("/")

    return url


async def probe_account(
    base_url: str,
    api_key: str,
    protocol: str = "openai",
    proxy: str = "auto",
    timeout_seconds: float = 8.0
) -> Dict[str, Any]:
    """
    Probes an API endpoint for reachability and model catalog without logging raw secrets.
    Returns: {
        "ok": bool,
        "normalized_base_url": str,
        "auth_header": str,
        "discovered_models": List[str],
        "latency_ms": int,
        "error": Optional[str]
    }
    """
    t0 = time.time()
    clean_key = (api_key or "").strip()
    proto = (protocol or "openai").strip().lower()
    norm_url = normalize_base_url(base_url, protocol=proto)

    # Prepare candidate paths for /models
    candidate_paths: List[str] = []
    if norm_url.endswith("/v1") or norm_url.endswith("/v1beta"):
        candidate_paths.append(f"{norm_url}/models")
        root_no_v1 = norm_url.rsplit("/", 1)[0]
        if root_no_v1 != norm_url:
            candidate_paths.append(f"{root_no_v1}/models")
    else:
        candidate_paths.append(f"{norm_url}/models")
        candidate_paths.append(f"{norm_url}/v1/models")

    # Determine auth headers to attempt
    headers_variants: List[tuple[str, Dict[str, str]]] = []
    if proto == "anthropic":
        headers_variants.append(("x-api-key", {
            "x-api-key": clean_key,
            "Authorization": f"Bearer {clean_key}",
            "anthropic-version": "2023-06-01"
        }))
        headers_variants.append(("Bearer", {
            "Authorization": f"Bearer {clean_key}",
            "anthropic-version": "2023-06-01"
        }))
    elif proto == "gemini":
        headers_variants.append(("Bearer", {
            "Authorization": f"Bearer {clean_key}",
            "x-goog-api-key": clean_key
        }))
    else:
        headers_variants.append(("Bearer", {
            "Authorization": f"Bearer {clean_key}"
        }))

    proxy_url = resolve_http_proxy(proxy)
    is_local = any(h in norm_url for h in ("127.0.0.1", "localhost", "::1", "0.0.0.0"))
    if is_local:
        proxy_url = None

    last_error = None
    discovered: List[str] = []
    chosen_auth_header = headers_variants[0][0]
    final_base_url = norm_url

    try:
        client_kwargs = {
            "timeout": timeout_seconds,
            "follow_redirects": True
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
            client_kwargs["trust_env"] = False
        else:
            client_kwargs["trust_env"] = not is_local

        async with httpx.AsyncClient(**client_kwargs) as client:
            # 1. Try each models path candidate and auth header
            for target_models_url in candidate_paths:
                for auth_name, headers in headers_variants:
                    try:
                        resp = await client.get(target_models_url, headers=headers)
                        latency = int((time.time() - t0) * 1000)

                        if resp.status_code == 200:
                            data = resp.json()
                            raw_models = []
                            if isinstance(data, dict):
                                raw_models = data.get("data") or data.get("models") or []
                            elif isinstance(data, list):
                                raw_models = data

                            parsed_models = []
                            for item in raw_models:
                                mid = item.get("id") if isinstance(item, dict) else (item if isinstance(item, str) else "")
                                cleaned = clean_and_filter_model_id(mid)
                                if cleaned and cleaned not in parsed_models:
                                    parsed_models.append(cleaned)

                            # If target was {base}/v1/models and norm_url didn't have /v1, update final_base_url
                            if target_models_url.endswith("/v1/models") and not norm_url.endswith("/v1"):
                                final_base_url = f"{norm_url}/v1"

                            return {
                                "ok": True,
                                "normalized_base_url": final_base_url,
                                "auth_header": auth_name,
                                "discovered_models": parsed_models,
                                "latency_ms": latency,
                                "error": None
                            }
                        elif resp.status_code in (401, 403):
                            last_error = f"Ошибка авторизации (HTTP {resp.status_code}): проверьте правильность API-ключа"
                        elif resp.status_code in (404, 405, 501):
                            last_error = f"Эндпоинт {resp.status_code}: список /models не поддерживается сервером"
                        else:
                            last_error = f"Сервер вернул статус HTTP {resp.status_code}"
                    except httpx.RequestError as req_err:
                        last_error = f"Сетевая ошибка соединения: {req_err}"

            # 2. If /models is 404/405, verify if base server is reachable
            try:
                ping_resp = await client.get(norm_url)
                latency = int((time.time() - t0) * 1000)
                # If reachable (even 404/405/200), we consider endpoint connected but /models not supported
                if ping_resp.status_code in (200, 404, 405):
                    return {
                        "ok": True,
                        "normalized_base_url": norm_url,
                        "auth_header": chosen_auth_header,
                        "discovered_models": [],
                        "latency_ms": latency,
                        "error": "Эндпоинт доступен, но не поддерживает список /models (введите модели вручную)"
                    }
            except Exception:
                pass

    except Exception as e:
        last_error = str(e)

    latency = int((time.time() - t0) * 1000)
    return {
        "ok": False,
        "normalized_base_url": norm_url,
        "auth_header": chosen_auth_header,
        "discovered_models": [],
        "latency_ms": latency,
        "error": last_error or "Не удалось подключиться к эндпоинту"
    }
