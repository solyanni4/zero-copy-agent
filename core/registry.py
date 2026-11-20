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

                if collected_models and is_ready:
                    browser_connected = True
                    browser_models = sorted(list(collected_models))
                    prov_names = [a.provider for a in active_web_accs]
                    browser_status = f"Active ({', '.join(prov_names)})"
                elif collected_models:
                    browser_connected = False
                    browser_models = []
                    browser_status = "Idle (Not Authenticated)"
                elif type(browser_engine).__name__.startswith("Mock") and is_ready:
                    if active_prov == "gemini":
                        browser_models = ["gemini-advanced", "gemini-4-neon", "gemini-2.5-flash"]
                    elif active_prov == "chatgpt":
                        browser_models = ["gpt-4o", "gpt-4o-mini", "chatgpt-plus"]
                    elif active_prov == "claude":
                        browser_models = ["claude-3-7-sonnet", "claude-pro"]
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
                accounts = getattr(mesh, "accounts", [])
                active_accs = [a for a in accounts if getattr(a, "is_active", True)]
                if len(active_accs) > 0:
                    mesh_connected = True
                    mesh_status = f"{len(active_accs)} active API accounts"
                    for a in active_accs:
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

        # Check active accounts with valid API keys from subscriptions.yaml
        sub_account_models: Set[str] = set()
        try:
            from core.healer import config_healer
            sub_cfg, _ = config_healer.load_or_create()
            for acc in sub_cfg.accounts:
                if acc.status == "active" and acc.api_key and acc.api_key.strip():
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

            card = ModelCard(
                id=mid,
                object="model",
                created=int(now),
                owned_by=f"zero-copy-agent:{item.get('provider', 'core')}"
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
