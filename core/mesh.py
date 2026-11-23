"""
Multi-Account Quota Mesh & Circuit Breaker for zero-copy-agent.
Distributes requests across multiple API keys and browser web-sessions per provider,
handles rate limits (429/5xx), tracks health, and performs seamless idempotent failover.
"""
import time
import logging
import threading
from typing import Dict, List, Optional, Any

logger = logging.getLogger("zcao.mesh")

class KeyState:
    def __init__(self, key: str, account_id: Optional[str] = None, auth_type: str = "api_key", models: Optional[List[str]] = None):
        self.key = key
        self.account_id = account_id or key
        self.auth_type = auth_type
        self.models = models or []
        self.consecutive_failures = 0
        self.tripped_until = 0.0
        self.total_requests = 0
        self.total_successes = 0

    @property
    def is_healthy(self) -> bool:
        return time.time() >= self.tripped_until

    def trip(self, cooldown_seconds: float = 300.0):
        self.tripped_until = time.time() + cooldown_seconds
        id_display = self.account_id if self.auth_type == "browser_session" else f"...{self.key[-4:] if len(self.key) > 4 else '***'}"
        logger.warning(f"[QuotaMesh] Circuit tripped for account/key '{id_display}'. Cooldown: {cooldown_seconds}s")

    def reset(self):
        self.consecutive_failures = 0
        self.tripped_until = 0.0

class QuotaMesh:
    """Manages multi-account keys and browser sessions with health failover across providers."""
    def __init__(self):
        # provider -> list of KeyState (api_key)
        self._pools: Dict[str, List[KeyState]] = {}
        self._index: Dict[str, int] = {}
        # provider -> list of KeyState (browser_session)
        self._web_pools: Dict[str, List[KeyState]] = {}
        self._web_index: Dict[str, int] = {}
        self._lock = threading.Lock()

    def sync_active_accounts(self, accounts: List[Any]):
        """Atomically synchronizes registered and active provider keys and web sessions preserving health state."""
        with self._lock:
            new_pools: Dict[str, List[KeyState]] = {}
            new_web_pools: Dict[str, List[KeyState]] = {}
            for acc in accounts:
                status = getattr(acc, "status", "active")
                if status != "active":
                    continue
                provider = getattr(acc, "provider", "").lower()
                auth_type = getattr(acc, "auth_type", "api_key")
                acc_id = getattr(acc, "id", "")

                acc_models = []
                for m in getattr(acc, "models", []):
                    m_id = m.id if hasattr(m, "id") else (m.get("id") if isinstance(m, dict) else str(m))
                    if m_id:
                        acc_models.append(m_id)

                api_key = getattr(acc, "api_key", None)
                if api_key and str(api_key).strip():
                    clean_key = str(api_key).strip()
                    if provider not in new_pools:
                        new_pools[provider] = []
                    existing_state = next((k for k in self._pools.get(provider, []) if k.key == clean_key), None)
                    if existing_state:
                        existing_state.account_id = acc_id or existing_state.account_id
                        existing_state.models = acc_models or existing_state.models
                        new_pools[provider].append(existing_state)
                    else:
                        new_pools[provider].append(KeyState(clean_key, account_id=acc_id, auth_type="api_key", models=acc_models))

                elif auth_type == "browser_session":
                    if provider not in new_web_pools:
                        new_web_pools[provider] = []
                    existing_state = next((k for k in self._web_pools.get(provider, []) if k.account_id == acc_id), None)
                    if existing_state:
                        existing_state.models = acc_models or existing_state.models
                        new_web_pools[provider].append(existing_state)
                    else:
                        new_web_pools[provider].append(KeyState(acc_id, account_id=acc_id, auth_type="browser_session", models=acc_models))

            self._pools = new_pools
            self._web_pools = new_web_pools
            for prov in new_pools:
                if prov not in self._index:
                    self._index[prov] = 0
            for prov in new_web_pools:
                if prov not in self._web_index:
                    self._web_index[prov] = 0

    def register_keys(self, provider: str, keys: List[str]):
        """Registers a list of API keys for a provider."""
        provider_name = provider.lower()
        if provider_name not in self._pools:
            self._pools[provider_name] = []
            self._index[provider_name] = 0

        existing = {k.key for k in self._pools[provider_name]}
        for i, key in enumerate(keys):
            if key and key not in existing:
                clean_key = key.strip()
                self._pools[provider_name].append(
                    KeyState(clean_key, account_id=f"{provider_name}-key-{i+1}", auth_type="api_key")
                )

    def get_candidate_accounts(self, provider: str, model_id: Optional[str] = None) -> List[KeyState]:
        """
        Returns all accounts for the given provider strictly ordered:
        1. API keys first (auth_type == 'api_key'), healthy first, then tripped keys by earliest recovery.
        2. Web sessions second (auth_type == 'browser_session'), healthy first, then tripped web sessions by recovery.
        """
        provider_name = provider.lower()
        ordered: List[KeyState] = []

        def _supports_model(state: KeyState) -> bool:
            if not model_id or not state.models:
                return True
            clean_m = model_id.lower()
            return any(
                clean_m == sm.lower() or sm.lower().endswith(clean_m) or clean_m.endswith(sm.lower())
                for sm in state.models
            )

        # 1. API Keys (Strict Priority)
        api_pool = self._pools.get(provider_name, [])
        if api_pool:
            start_idx = self._index.get(provider_name, 0)
            n = len(api_pool)
            rotated = [api_pool[(start_idx + i) % n] for i in range(n)]

            matching = [k for k in rotated if _supports_model(k)]
            candidates = matching if matching else rotated

            healthy = [k for k in candidates if k.is_healthy]
            cooling = sorted([k for k in candidates if not k.is_healthy], key=lambda k: k.tripped_until)
            ordered.extend(healthy + cooling)

        # 2. Browser Web Sessions (Strict Secondary Fallback)
        web_pool = self._web_pools.get(provider_name, [])
        if web_pool:
            start_idx = self._web_index.get(provider_name, 0)
            n = len(web_pool)
            rotated = [web_pool[(start_idx + i) % n] for i in range(n)]

            matching = [k for k in rotated if _supports_model(k)]
            candidates = matching if matching else rotated

            healthy = [k for k in candidates if k.is_healthy]
            cooling = sorted([k for k in candidates if not k.is_healthy], key=lambda k: k.tripped_until)
            ordered.extend(healthy + cooling)

        return ordered

    def get_active_key(self, provider: str) -> Optional[str]:
        """Returns the next healthy API key using round-robin rotation."""
        provider_name = provider.lower()
        pool = self._pools.get(provider_name, [])
        if not pool:
            return None

        start_idx = self._index.get(provider_name, 0)
        n = len(pool)
        for i in range(n):
            idx = (start_idx + i) % n
            state = pool[idx]
            if state.is_healthy:
                self._index[provider_name] = (idx + 1) % n
                state.total_requests += 1
                return state.key

        soonest = min(pool, key=lambda s: s.tripped_until)
        logger.warning(f"[QuotaMesh] All keys for {provider_name} in cooldown. Falling back to key with earliest recovery.")
        return soonest.key

    def get_active_web_account(self, provider: str) -> Optional[str]:
        """Returns the next healthy browser_session account ID using round-robin rotation."""
        provider_name = provider.lower()
        pool = self._web_pools.get(provider_name, [])
        if not pool:
            return None

        start_idx = self._web_index.get(provider_name, 0)
        n = len(pool)
        for i in range(n):
            idx = (start_idx + i) % n
            state = pool[idx]
            if state.is_healthy:
                self._web_index[provider_name] = (idx + 1) % n
                state.total_requests += 1
                return state.account_id

        soonest = min(pool, key=lambda s: s.tripped_until)
        return soonest.account_id

    def record_web_success(self, provider: str, account_id: str):
        """Marks web account request success."""
        provider_name = provider.lower()
        pool = self._web_pools.get(provider_name, [])
        for state in pool:
            if state.account_id == account_id:
                state.total_successes += 1
                state.reset()
                break

    def record_web_failure(self, provider: str, account_id: str):
        """Records error on web account and trips breaker after repeated failures."""
        provider_name = provider.lower()
        pool = self._web_pools.get(provider_name, [])
        for state in pool:
            if state.account_id == account_id:
                state.consecutive_failures += 1
                if state.consecutive_failures >= 3:
                    state.trip(cooldown_seconds=120.0)
                break

    def record_success(self, provider: str, key: str):
        """Marks request success and resets failure counter."""
        provider_name = provider.lower()
        pool = self._pools.get(provider_name, [])
        for state in pool:
            if state.key == key:
                state.total_successes += 1
                state.reset()
                break

    def record_failure(self, provider: str, key: str, status_code: Optional[int] = None):
        """Records error and trips circuit breaker upon 429 or persistent errors."""
        provider_name = provider.lower()
        pool = self._pools.get(provider_name, [])
        for state in pool:
            if state.key == key:
                state.consecutive_failures += 1
                if status_code in (429, 402):
                    state.trip(cooldown_seconds=300.0)
                elif state.consecutive_failures >= 3:
                    state.trip(cooldown_seconds=120.0)
                break

    def get_mesh_status(self) -> Dict[str, dict]:
        """Returns diagnostic status of all registered pools."""
        summary = {}
        for provider, pool in self._pools.items():
            healthy = sum(1 for k in pool if k.is_healthy)
            summary[provider] = {
                "total_keys": len(pool),
                "healthy_keys": healthy,
                "tripped_keys": len(pool) - healthy
            }
        for provider, pool in self._web_pools.items():
            healthy = sum(1 for k in pool if k.is_healthy)
            web_key = f"{provider}_web"
            summary[web_key] = {
                "total_accounts": len(pool),
                "healthy_accounts": healthy,
                "tripped_accounts": len(pool) - healthy
            }
        return summary

    def get_active_accounts(self) -> List[Dict[str, Any]]:
        """Returns active accounts registered in the quota mesh with healthy key pools."""
        accounts = []
        for provider, pool in self._pools.items():
            healthy = [k for k in pool if k.is_healthy]
            if healthy:
                accounts.append({
                    "id": f"{provider}-mesh",
                    "provider": provider,
                    "auth_type": "api_key",
                    "status": "active",
                    "keys_count": len(pool),
                    "healthy_count": len(healthy),
                })
        for provider, pool in self._web_pools.items():
            healthy = [k for k in pool if k.is_healthy]
            if healthy:
                for k in healthy:
                    accounts.append({
                        "id": k.account_id,
                        "provider": provider,
                        "auth_type": "browser_session",
                        "status": "active",
                        "keys_count": 1,
                        "healthy_count": 1,
                    })
        return accounts

    def clear(self):
        """Clears all registered key pools and indices."""
        with self._lock:
            self._pools.clear()
            self._index.clear()
            self._web_pools.clear()
            self._web_index.clear()


mesh = QuotaMesh()
