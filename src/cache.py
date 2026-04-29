"""In-memory LRU cache with TTL support for UniMail.

Provides caching for inbox listings and message details to reduce
redundant connector calls.
"""

from __future__ import annotations

import time
import threading
from collections import OrderedDict
from typing import Any, Optional

from .config import get_config
from .log import get_logger

logger = get_logger(__name__)


class TTLCache:
    """Thread-safe LRU cache with per-entry TTL expiration."""

    def __init__(self, maxsize: int = 256, default_ttl: int = 60):
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache. Returns None if expired or missing."""
        with self._lock:
            if key not in self._cache:
                return None
            value, expires_at = self._cache[key]
            if time.time() > expires_at:
                # Expired
                del self._cache[key]
                return None
            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set a value in cache with optional custom TTL."""
        expires_at = time.time() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expires_at)
            # Evict oldest if over capacity
            while len(self._cache) > self._maxsize:
                self._cache.popitem(last=False)

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache."""
        with self._lock:
            self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Remove all keys matching a prefix."""
        with self._lock:
            keys_to_remove = [k for k in self._cache if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._cache[k]

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


class MailCache:
    """High-level mail cache wrapping TTLCache with domain-specific methods.

    Caches:
    - inbox listings (TTL from config, default 60s)
    - message details (TTL from config, default 300s)
    """

    def __init__(self, enabled: bool = True, inbox_ttl: int = 60, message_ttl: int = 300):
        self._enabled = enabled
        self._inbox_ttl = inbox_ttl
        self._message_ttl = message_ttl
        self._inbox_cache = TTLCache(maxsize=64, default_ttl=inbox_ttl)
        self._message_cache = TTLCache(maxsize=512, default_ttl=message_ttl)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _inbox_key(
        self,
        account_id: Optional[str],
        folder: str,
        limit: int,
        unread_only: bool,
    ) -> str:
        return f"inbox:{account_id or 'all'}:{folder}:{limit}:{unread_only}"

    def get_inbox(
        self,
        account_id: Optional[str],
        folder: str,
        limit: int,
        unread_only: bool,
    ) -> Optional[list]:
        """Get cached inbox listing."""
        if not self._enabled:
            return None
        key = self._inbox_key(account_id, folder, limit, unread_only)
        result = self._inbox_cache.get(key)
        if result is not None:
            logger.debug(f"Cache HIT for inbox: {key}")
        return result

    def set_inbox(
        self,
        account_id: Optional[str],
        folder: str,
        limit: int,
        unread_only: bool,
        messages: list,
    ) -> None:
        """Cache inbox listing."""
        if not self._enabled:
            return
        key = self._inbox_key(account_id, folder, limit, unread_only)
        self._inbox_cache.set(key, messages, self._inbox_ttl)

    def get_message(self, message_id: str) -> Optional[Any]:
        """Get cached message detail."""
        if not self._enabled:
            return None
        result = self._message_cache.get(f"msg:{message_id}")
        if result is not None:
            logger.debug(f"Cache HIT for message: {message_id}")
        return result

    def set_message(self, message_id: str, message: Any) -> None:
        """Cache message detail."""
        if not self._enabled:
            return
        self._message_cache.set(f"msg:{message_id}", message, self._message_ttl)

    def invalidate(self, account_id: str) -> None:
        """Invalidate all cache entries for a specific account."""
        logger.debug(f"Invalidating cache for account: {account_id}")
        self._inbox_cache.invalidate_prefix(f"inbox:{account_id}")
        self._inbox_cache.invalidate_prefix("inbox:all:")
        # Don't invalidate individual messages - they're still valid

    def invalidate_all(self) -> None:
        """Clear all caches."""
        logger.debug("Invalidating all caches")
        self._inbox_cache.clear()
        self._message_cache.clear()


def create_mail_cache() -> MailCache:
    """Create a MailCache instance from the current config."""
    config = get_config()
    return MailCache(
        enabled=config.cache.enabled,
        inbox_ttl=config.cache.inbox_ttl,
        message_ttl=config.cache.message_ttl,
    )
