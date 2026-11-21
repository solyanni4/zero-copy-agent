"""
Multi-Account Quota Mesh & Circuit Breaker for zero-copy-agent.
Distributes requests across multiple API keys per provider, handles rate limits (429/5xx),
and performs seamless idempotent failover.
"""
import time
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("zcao.mesh")

class KeyState:
    def __init__(self, key: str):
        self.key = key
        self.consecutive_failures = 0
        self.tripped_until = 0.0
        self.total_requests = 0
        self.total_successes = 0

    @property
    def is_healthy(self) -> bool:
        return time.time() >= self.tripped_until

    def trip(self, cooldown_seconds: float = 300.0):
        self.tripped_until = time.time() + cooldown_seconds
        logger.warning(f"[QuotaMesh] Circuit tripped for key ending in '...{self.key[-4:] if len(self.key) > 4 else '***'}'. Cooldown: {cooldown_seconds}s")

    def reset(self):
        self.consecutive_failures = 0
        self.tripped_until = 0.0

class QuotaMesh:
    """Manages multi-account keys and health failover across providers."""
    def __init__(self):
        # provider -> list of KeyState
        self._pools: Dict[str, List[KeyState]] = {}
        self._index: Dict[str, int] = {}

    def register_keys(self, provider: str, keys: List[str]):
        """Registers a list of API keys for a provider."""
        provider_name = provider.lower()
        if provider_name not in self._pools:
            self._pools[provider_name] = []
            self._index[provider_name] = 0

        existing = {k.key for k in self._pools[provider_name]}
        for key in keys:
            if key and key not in existing:
                self._pools[provider_name].append(KeyState(key.strip()))

    def get_active_key(self, provider: str) -> Optional[str]:
        """Returns the next healthy API key using round-robin rotation."""
        provider_name = provider.lower()
        pool = self._pools.get(provider_name, [])
        if not pool:
            return None

        # Look for healthy keys in round-robin order
        start_idx = self._index.get(provider_name, 0)
        n = len(pool)
        for i in range(n):
            idx = (start_idx + i) % n
            state = pool[idx]
            if state.is_healthy:
                self._index[provider_name] = (idx + 1) % n
                state.total_requests += 1
                return state.key

        # If all keys are in cooldown, pick the one expiring soonest
        soonest = min(pool, key=lambda s: s.tripped_until)
        logger.warning(f"[QuotaMesh] All keys for {provider_name} in cooldown. Falling back to key with earliest recovery.")
        return soonest.key

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
                # Instant trip on 429 Too Many Requests or 402 Payment Required
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
        return accounts

    def clear(self):
        """Clears all registered key pools and indices."""
        self._pools.clear()
        self._index.clear()


mesh = QuotaMesh()
