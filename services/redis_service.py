"""Redis caching service for cross-call synchronization and pacing.

Handles:
- Immediate DND & opt-out lookups
- Single-active-call-per-number distributed locks
- Platform-wide CPS (Calls Per Second) pacing
- Login attempt counters
- Active call counts and circuit breakers
"""

import time
import logging
from typing import Optional
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None


class RedisService:
    """Manages Redis connection, distributed locks, DND checks, and rate limits."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._client = None
        # In-memory mock store fallback if Redis server is unavailable in testing
        self._mock_store: dict = {}
        self._mock_locks: dict = {}
        self._retry_at: float = 0.0

    async def get_client(self):
        """Initializes and returns async Redis client, or None if unavailable."""
        if self._client is None and aioredis is not None and time.monotonic() >= self._retry_at:
            try:
                self._client = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_timeout=2.0,
                    socket_connect_timeout=0.5,  # a stopped local server must not hold up a call
                )
                await self._client.ping()
            except Exception as e:
                logger.warning(
                    f"Unable to connect to Redis at {self.redis_url}: {e}. "
                    f"Falling back to resilient in-memory synchronization."
                )
                self._client = None
                self._retry_at = time.monotonic() + 30  # do not block every call on reconnects
        return self._client

    # --------------------------------------------------------------------------
    # DND & Opt-Out Immediate Filtering
    # --------------------------------------------------------------------------
    async def is_dnd_or_opted_out(self, phone_number: str, client_id: str) -> bool:
        """Check whether a phone number opted out of this client's calls."""
        normalized_phone = phone_number.strip().replace(" ", "").replace("-", "")
        key = f"dnd:optout:{client_id}:{normalized_phone}"

        client = await self.get_client()
        if client:
            try:
                val = await client.get(key)
                return val is not None
            except Exception as e:
                logger.error(f"Redis DND lookup error: {e}")

        # Fallback in-memory
        return self._mock_store.get(key) is not None

    async def mark_opted_out(self, phone_number: str, client_id: str, reason: str = "customer_request") -> bool:
        """Immediately record a client's opt-out across all workers and dialers."""
        normalized_phone = phone_number.strip().replace(" ", "").replace("-", "")
        key = f"dnd:optout:{client_id}:{normalized_phone}"
        value = f"opted_out:{int(time.time())}:{reason}"

        client = await self.get_client()
        if client:
            try:
                # Never expires: an opt-out is permanent (Postgres dnd_registry is the record)
                await client.set(key, value)
                return True
            except Exception as e:
                logger.error(f"Redis mark_opted_out error: {e}")

        self._mock_store[key] = value
        return True

    # --------------------------------------------------------------------------
    # Single-Active-Call-Per-Number Distributed Lock
    # --------------------------------------------------------------------------
    async def acquire_call_lock(self, phone_number: str, call_id: str) -> bool:
        """Prevent dialing the same lead concurrently from multiple workers.
        
        Returns True if the lock was acquired, False if a call is already active.
        """
        normalized_phone = phone_number.strip().replace(" ", "").replace("-", "")
        key = f"lock:active_call:{normalized_phone}"
        ttl = settings.REDIS_CALL_LOCK_TTL_SECONDS

        client = await self.get_client()
        if client:
            try:
                # NX: Only set the key if it does not already exist
                acquired = await client.set(key, call_id, ex=ttl, nx=True)
                return bool(acquired)
            except Exception as e:
                logger.error(f"Redis lock acquisition error: {e}")

        # In-memory fallback
        now = time.time()
        lock_entry = self._mock_locks.get(key)
        if lock_entry and lock_entry["expires"] > now:
            return False  # Already locked

        self._mock_locks[key] = {"call_id": call_id, "expires": now + ttl}
        return True

    async def release_call_lock(self, phone_number: str, call_id: str) -> bool:
        """Releases active call lock for a phone number."""
        normalized_phone = phone_number.strip().replace(" ", "").replace("-", "")
        key = f"lock:active_call:{normalized_phone}"

        client = await self.get_client()
        if client:
            try:
                current_val = await client.get(key)
                if current_val == call_id:
                    await client.delete(key)
                    return True
                return False
            except Exception as e:
                logger.error(f"Redis lock release error: {e}")

        lock_entry = self._mock_locks.get(key)
        if lock_entry and lock_entry.get("call_id") == call_id:
            del self._mock_locks[key]
            return True
        return False

    # --------------------------------------------------------------------------
    # Platform-Wide Calls-Per-Second (CPS) Pacing
    # --------------------------------------------------------------------------
    async def check_and_consume_cps_token(self, max_cps: Optional[int] = None) -> bool:
        """Enforces the carrier CPS limit, which is shared by every client, in 1-second windows."""
        limit = max_cps or settings.PLIVO_MAX_CALLS_PER_SECOND
        current_second = int(time.time())
        key = f"pacing:cps:{current_second}"

        client = await self.get_client()
        if client:
            try:
                count = await client.incr(key)
                if count == 1:
                    await client.expire(key, 2)  # Auto-expire after 2 seconds
                return count <= limit
            except Exception as e:
                logger.error(f"Redis CPS limiter error: {e}")

        # In-memory fallback
        current_count = self._mock_store.get(key, 0) + 1
        self._mock_store[key] = current_count
        return current_count <= limit


    # --------------------------------------------------------------------------
    # Login attempt counters
    # --------------------------------------------------------------------------
    async def get_counter(self, key: str) -> int:
        client = await self.get_client()
        if client:
            try:
                return int(await client.get(key) or 0)
            except Exception as e:
                logger.error(f"Redis counter read error: {e}")
        entry = self._mock_locks.get(key)
        return entry["count"] if entry and entry["expires"] > time.time() else 0

    async def incr_counter(self, key: str, ttl_seconds: int) -> None:
        client = await self.get_client()
        if client:
            try:
                if await client.incr(key) == 1:
                    await client.expire(key, ttl_seconds)
                return
            except Exception as e:
                logger.error(f"Redis counter write error: {e}")
        count = await self.get_counter(key)
        self._mock_locks[key] = {"count": count + 1, "expires": time.time() + ttl_seconds}

    async def clear_counter(self, key: str) -> None:
        client = await self.get_client()
        if client:
            try:
                await client.delete(key)
                return
            except Exception as e:
                logger.error(f"Redis counter delete error: {e}")
        self._mock_locks.pop(key, None)

    # --------------------------------------------------------------------------
    # Human sales desk availability for warm transfers
    # --------------------------------------------------------------------------
    async def pick_free_human_agent(self, numbers: list, hold_seconds: int = 900) -> Optional[str]:
        """Reserves the first sales-desk number not already on a transfer."""
        client = await self.get_client()
        for number in numbers:
            key = f"human:busy:{number}"
            if client:
                try:
                    if await client.set(key, "1", ex=hold_seconds, nx=True):
                        return number
                    continue
                except Exception as e:
                    logger.error(f"Redis human-agent reservation error: {e}")
            entry = self._mock_locks.get(key)
            if not entry or entry["expires"] <= time.time():
                self._mock_locks[key] = {"call_id": "transfer", "expires": time.time() + hold_seconds}
                return number
        return None


_instance: Optional[RedisService] = None


def get_redis_service() -> RedisService:
    """Singleton getter for RedisService."""
    global _instance
    if _instance is None:
        _instance = RedisService()
    return _instance

