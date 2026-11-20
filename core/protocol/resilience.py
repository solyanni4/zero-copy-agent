"""
Resilience & Fault Tolerance Primitives for ZCAP.
Includes Circuit Breaker, Watchdog Timer, and Idempotency deduplication.
"""
import asyncio
import time
from typing import Dict, Optional, Set

class CircuitBreakerOpenException(Exception):
    """Raised when circuit breaker is tripped open."""
    pass

class CircuitBreaker:
    """Circuit Breaker pattern to protect agents and web providers from cascade failure."""
    def __init__(self, failure_threshold: int = 3, recovery_timeout: float = 15.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def record_success(self):
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"

    def can_execute(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        if self.state == "HALF_OPEN":
            return True
        return False

class WatchdogTimer:
    """Dead Man's Switch to detect hung generations or broken sockets."""
    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds
        self.last_activity = time.time()

    def touch(self):
        """Resets the watchdog timer."""
        self.last_activity = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_activity) > self.timeout_seconds

class IdempotencyCache:
    """Deduplication cache to guarantee at-least-once delivery without duplicate execution."""
    def __init__(self, ttl_seconds: float = 300.0):
        self.ttl = ttl_seconds
        self.cache: Dict[str, float] = {}

    def check_and_set(self, request_id: str) -> bool:
        """Returns True if request_id is new, False if already seen."""
        now = time.time()
        self._cleanup(now)
        if request_id in self.cache:
            return False
        self.cache[request_id] = now
        return True

    def _cleanup(self, now: float):
        expired = [k for k, ts in self.cache.items() if now - ts > self.ttl]
        for k in expired:
            del self.cache[k]
