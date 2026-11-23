"""
Dynamic Connected-Only Model Selector & Provider Health Registry.
Filters available models so that only currently connected and ready-to-infer
models appear in /v1/models and UI dropdowns.
"""

import time
import logging
from typing import List, Dict, Any, Optional, Set
from pydantic import BaseModel, Field

from core.models import ModelCard
from core.attribution import get_display_name

logger = logging.getLogger("zcao.registry")


def classify_model(model_id: str, provider: str = "") -> Dict[str, Any]:
    """
    Classifies model power and speed tier with human badges:
    - '🔥 Сильная': flagship/reasoning models (pro, opus, reasoner, o1, o3, max, neon, advanced, r1).
    - '⚡ Быстрая': fast models (flash, sonnet, mini, haiku, turbo, plus).
    - '🪶 Базовая': lightweight/small models (lite, nano, 3b, 7b, small).
    - '🖥️ Локальная GPU': local Ollama models (14b and higher: '🔥 Сильная · Локальная GPU').
    """
    import re
    low = (model_id or "").lower()
    prov_low = (provider or "").lower()
    is_ollama = prov_low == "ollama" or low.startswith("ollama/") or any(k in low for k in ("coder:14b", "coder:32b", ":7b", ":14b", ":32b"))

    tokens = set(re.split(r"[-_:\.\s/]+", low))
    strong_keywords = ["pro", "opus", "reasoner", "o1", "o3", "max", "neon", "advanced", "r1"]
    fast_keywords = ["flash", "sonnet", "mini", "haiku", "turbo", "plus"]
    base_keywords = ["lite", "nano", "3b", "7b", "small"]

    def has_kw(k_list):
        for k in k_list:
            if k in tokens:
                return True
            if any(t.startswith(k) or t.endswith(k) for t in tokens if t != "gemini"):
                return True
        return False

    if is_ollama:
        is_large = any(s in low for s in ("14b", "32b", "70b", "r1-14b", "r1-32b", "r1-70b"))
        if is_large or "coder:14b" in low or "coder:32b" in low:
            return {
                "tier": "strong_local",
                "badge": "🔥 Сильная · Локальная GPU",
                "short_badge": "🔥 Сильная · Локальная GPU",
                "is_local": True,
                "icon": "🖥️"
            }
        return {
            "tier": "local",
            "badge": "🖥️ Локальная GPU",
            "short_badge": "🖥️ Локальная GPU",
            "is_local": True,
            "icon": "🖥️"
        }

    if has_kw(strong_keywords):
        return {
            "tier": "strong",
            "badge": "🔥 Сильная",
            "short_badge": "🔥 Сильная",
            "is_local": False,
            "icon": "🔥"
        }
    if has_kw(fast_keywords):
        return {
            "tier": "fast",
            "badge": "⚡ Быстрая",
            "short_badge": "⚡ Быстрая",
            "is_local": False,
            "icon": "⚡"
        }
    if has_kw(base_keywords):
        return {
            "tier": "base",
            "badge": "🪶 Базовая",
            "short_badge": "🪶 Базовая",
            "is_local": False,
            "icon": "🪶"
        }

    return {
        "tier": "fast",
        "badge": "⚡ Быстрая",
        "short_badge": "⚡ Быстрая",
        "is_local": False,
        "icon": "⚡"
    }


def format_model_with_badge(model_id: str, provider: str = "") -> str:
    """Formats model name with readable class badge, e.g. 'Gemini 3.8 Flash [⚡ Быстрая]'."""
    info = classify_model(model_id, provider)
    display = get_display_name(model_id)
    return f"{display} [{info['short_badge']}]"


def get_best_replacement_model(missing_model: str, role: str, available_models: List[str]) -> Optional[str]:
    """
    Selects the best available replacement model when a chosen model becomes unavailable.
    - For architect / reviewer: prefers '🔥 Сильная', then '⚡ Быстрая'.
    - For executor / coder: prefers '🖥️ Локальная GPU', then '⚡ Быстрая'.
    """
    if not available_models:
        return None

    role_low = (role or "").lower()
    is_architect_like = any(r in role_low for r in ("arch", "plan", "rev", "audit"))

    scored = []
    for m in available_models:
        info = classify_model(m)
        tier = info["tier"]
        score = 0
        if is_architect_like:
            if tier == "strong":
                score = 100
            elif tier == "strong_local":
                score = 80
            elif tier == "fast":
                score = 60
            elif tier == "local":
                score = 40
            else:
                score = 20
        else:
            # executor / coder
            if tier == "strong_local":
                score = 100
            elif tier == "local":
                score = 85
            elif tier == "fast":
                score = 70
            elif tier == "strong":
                score = 50
            else:
                score = 20
        scored.append((score, m))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else available_models[0]

class ProviderHealth(BaseModel):
    name: str
    connected: bool
    status_text: str
    last_checked: float = Field(default_factory=time.time)
    available_models: List[str] = Field(default_factory=list)

class ModelRegistry:
    """
    Tracks inference sources (Ollama GPU, Web sessions, Cloud API pools)
    and dynamically filters models down to only those that are currently active.
    """

    def __init__(self, ttl_seconds: float = 20.0):
        self.ttl_seconds = ttl_seconds
        self._last_refresh = 0.0
        self._cached_active_cards: List[ModelCard] = []
        self._cached_all_cards: List[ModelCard] = []
        self._provider_health: Dict[str, ProviderHealth] = {}
        self._subscriptions_mtime: float = 0.0
        self._config_mtime: float = 0.0

    def check_disk_updates(self) -> bool:
        """Returns True and updates tracking if subscriptions.yaml or config.yaml mtime changed."""
        changed = False
        try:
            from core.healer import config_healer
            sub_path = config_healer.target_path
            if sub_path.exists():
                mtime = sub_path.stat().st_mtime
                if mtime != self._subscriptions_mtime:
                    self._subscriptions_mtime = mtime
                    changed = True
        except Exception:
            pass

        try:
            from core.config import get_runtime_cwd
            cfg_path = get_runtime_cwd() / "config.yaml"
            if cfg_path.exists():
                mtime = cfg_path.stat().st_mtime
                if mtime != self._config_mtime:
                    self._config_mtime = mtime
                    changed = True
        except Exception:
            pass

        return changed

    async def check_providers(
        self,
        ollama_driver: Any = None,
        browser_engine: Any = None,
        mesh: Any = None
    ) -> Dict[str, ProviderHealth]:
        """Polls connected providers and records their live health status."""
        health: Dict[str, ProviderHealth] = {}
        now = time.time()

        # 1. Local Ollama GPU
        ollama_connected = False
        ollama_models: List[str] = []
        ollama_status = "Offline"
        if ollama_driver:
            try:
                if await ollama_driver.is_authenticated():
                    raw_models = await ollama_driver.list_models()
                    if raw_models:
                        ollama_connected = True
                        ollama_models = raw_models
                        ollama_status = f"Online ({len(ollama_models)} models)"
                    else:
                        ollama_connected = False
                        ollama_status = "Online (0 models)"
            except Exception as e:
                logger.debug(f"[Registry] Ollama health check error: {e}")
                ollama_status = f"Error: {e}"

        health["ollama"] = ProviderHealth(
            name="ollama",
            connected=ollama_connected,
            status_text=ollama_status,
            last_checked=now,
            available_models=ollama_models
        )

        # 2. Browser Session Engine (Gemini / ChatGPT / Claude Web)
        browser_connected = False
        browser_models: List[str] = []
        browser_status = "Not Initialized"
        if browser_engine:
            try:
                active_prov = getattr(browser_engine, "active_provider", "gemini")
                is_ready = getattr(browser_engine, "is_ready", False)

                from core.healer import config_healer
                sub_cfg, _ = config_healer.load_or_create()
                active_web_accs = [
                    a for a in sub_cfg.accounts
                    if a.auth_type == "browser_session" and a.status == "active"
                ]

                collected_models = set()
                for acc in active_web_accs:
                    for m in acc.models:
                        collected_models.add(m.id)

                if collected_models:
                    browser_connected = True
                    browser_models = sorted(list(collected_models))
                    prov_names = [a.provider for a in active_web_accs]
                    browser_status = f"Active ({', '.join(prov_names)})"
                elif type(browser_engine).__name__.startswith("Mock") and is_ready:
                    if active_prov == "gemini":
                        browser_models = ["gemini-advanced", "gemini-4-neon", "gemini-2.5-flash"]
                    elif active_prov == "chatgpt":
                        browser_models = ["gpt-4o", "gpt-4o-mini", "chatgpt-plus"]
                    elif active_prov == "claude":
                        browser_models = ["claude-3-7-sonnet", "claude-pro"]
                    elif active_prov == "deepseek":
                        browser_models = ["deepseek-chat", "deepseek-reasoner"]
                    elif active_prov == "qwen":
                        browser_models = ["qwen-max", "qwen-coder", "qwq"]
                    elif active_prov == "grok":
                        browser_models = ["grok-3", "grok-3-mini"]
                    browser_connected = True
                    browser_status = f"Active ({active_prov})"
                else:
                    browser_connected = False
                    browser_models = []
                    browser_status = "Idle (No Sessions)"
            except Exception as e:
                logger.debug(f"[Registry] Browser engine check error: {e}")
                browser_status = f"Error: {e}"

        health["browser"] = ProviderHealth(
            name="browser",
            connected=browser_connected,
            status_text=browser_status,
            last_checked=now,
            available_models=browser_models
        )

        # 3. Quota Mesh (Cloud API Keys)
        mesh_connected = False
        mesh_models: List[str] = []
        mesh_status = "No Accounts"
        if mesh:
            try:
                if hasattr(mesh, "get_active_accounts"):
                    active_accs = mesh.get_active_accounts()
                else:
                    accounts = getattr(mesh, "accounts", [])
                    active_accs = [a for a in accounts if getattr(a, "is_active", True)]

                if len(active_accs) > 0:
                    mesh_connected = True
                    mesh_status = f"{len(active_accs)} active API accounts"
                    from core.healer import DEFAULT_PROVIDER_MODELS
                    for a in active_accs:
                        prov = (a.get("provider") if isinstance(a, dict) else getattr(a, "provider", "")).lower()
                        if prov in ("openai", "chatgpt"):
                            mesh_models.extend(["gpt-4o", "gpt-4o-mini", "o1", "o3-mini"])
                        elif prov == "anthropic":
                            mesh_models.extend(["claude-3-7-sonnet", "claude-3-5-sonnet", "claude-3-haiku"])
                        elif prov == "gemini":
                            mesh_models.extend(["gemini-2.5-flash", "gemini-2.5-pro"])
                        elif prov in DEFAULT_PROVIDER_MODELS:
                            mesh_models.extend(DEFAULT_PROVIDER_MODELS[prov])
                        elif isinstance(a, dict):
                            mesh_models.extend(a.get("models", []))
                        else:
                            mesh_models.extend(getattr(a, "supported_models", []))
            except Exception as e:
                logger.debug(f"[Registry] Mesh check error: {e}")
                mesh_status = f"Error: {e}"

        health["mesh"] = ProviderHealth(
            name="mesh",
            connected=mesh_connected,
            status_text=mesh_status,
            last_checked=now,
            available_models=list(set(mesh_models))
        )

        self._provider_health = health
        return health

    async def get_models(
        self,
        include_inactive: bool = False,
        ollama_driver: Any = None,
        browser_engine: Any = None,
        mesh: Any = None,
        aliases: Optional[Dict[str, str]] = None,
        force_refresh: bool = False
    ) -> List[ModelCard]:
        """
        Returns list of ModelCards.
        By default (include_inactive=False), ONLY returns models that have
        an active, ready inference source.
        """
        if self.check_disk_updates():
            force_refresh = True

        now = time.time()
        if not force_refresh and (now - self._last_refresh) < self.ttl_seconds:
            if include_inactive:
                return self._cached_all_cards
            return self._cached_active_cards

        health = await self.check_providers(ollama_driver, browser_engine, mesh)
        active_ids: Set[str] = set()
        all_catalog: List[Dict[str, Any]] = []

        # 1. Ollama models
        ollama_h = health.get("ollama")
        if ollama_h and ollama_h.connected:
            for m in ollama_h.available_models:
                active_ids.add(m)
                active_ids.add(f"ollama/{m}")
                all_catalog.append({
                    "id": m,
                    "provider": "ollama",
                    "connected": True,
                    "description": f"Local Ollama weights: {m}"
                })

        # 2. Browser session models & Catalog definitions
        browser_h = health.get("browser")
        active_browser_models = set(browser_h.available_models) if (browser_h and browser_h.connected) else set()

        # Check active accounts with valid API keys or active browser sessions from subscriptions.yaml
        sub_account_models: Set[str] = set()
        try:
            from core.healer import config_healer
            sub_cfg, _ = config_healer.load_or_create()
            for acc in sub_cfg.accounts:
                if acc.status == "active":
                    if (acc.api_key and acc.api_key.strip()) or acc.auth_type == "browser_session":
                        for m in acc.models:
                            if m.status == "active":
                                sub_account_models.add(m.id)
        except Exception:
            pass

        known_browser_catalog = [
            ("gemini-advanced", "gemini"),
            ("gemini-4-neon", "gemini"),
            ("gemini-2.5-flash", "gemini"),
            ("gpt-4o", "chatgpt"),
            ("gpt-4o-mini", "chatgpt"),
            ("chatgpt-plus", "chatgpt"),
            ("claude-3-7-sonnet", "claude"),
            ("claude-pro", "claude"),
        ]

        for mid, prov in known_browser_catalog:
            is_conn = (mid in active_browser_models) or (mid in sub_account_models)
            if is_conn:
                active_ids.add(mid)
                all_catalog.append({
                    "id": mid,
                    "provider": prov,
                    "connected": True,
                    "description": f"Live Session / API Key ({prov}): {mid}"
                })
            else:
                all_catalog.append({
                    "id": mid,
                    "provider": prov,
                    "connected": False,
                    "description": f"Provider ({prov} - No Active Key / Session): {mid}"
                })

        # 3. Dynamic aliases
        effective_aliases = aliases or {}
        for alias_id, target in effective_aliases.items():
            # An alias is connected if its target is in active_ids
            # Or if alias is cascade:architect and both browser + ollama/browser exist
            is_conn = False
            if target == "cascade:architect":
                # Cascade works if at least browser is ready or ollama is ready
                is_conn = (browser_h and browser_h.connected) or (ollama_h and ollama_h.connected)
            elif target in active_ids or (ollama_h and target in ollama_h.available_models):
                is_conn = True
            elif browser_h and browser_h.connected and target in browser_h.available_models:
                is_conn = True

            if is_conn:
                active_ids.add(alias_id)

            all_catalog.append({
                "id": alias_id,
                "provider": "alias",
                "connected": is_conn,
                "description": f"Dynamic alias -> {target}"
            })

        # 4. Standard Mock Test
        all_catalog.append({
            "id": "mock-test",
            "provider": "mock",
            "connected": True,
            "description": "Zero-Copy Mock Engine (Instant Test)"
        })
        active_ids.add("mock-test")

        # Deduplicate and build ModelCard instances
        seen_ids: Set[str] = set()
        active_cards: List[ModelCard] = []
        all_cards: List[ModelCard] = []

        for item in all_catalog:
            mid = item["id"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)

            prov = item.get("provider", "core")
            c_info = classify_model(mid, prov)
            is_conn = item.get("connected", False) or mid in active_ids

            card = ModelCard(
                id=mid,
                object="model",
                created=int(now),
                owned_by=f"zero-copy-agent:{prov}",
                tier=c_info["tier"],
                badge=c_info["badge"],
                short_badge=c_info["short_badge"],
                status="active" if is_conn else "disabled",
                display_name=format_model_with_badge(mid, prov),
                is_local=c_info["is_local"]
            )

            all_cards.append(card)
            if item.get("connected", False) or mid in active_ids:
                active_cards.append(card)

        self._cached_active_cards = active_cards
        self._cached_all_cards = all_cards
        self._last_refresh = now

        if include_inactive:
            return all_cards
        return active_cards

# Global registry singleton
model_registry = ModelRegistry()
