"""Small in-memory TTL cache for read-only Ninebot responses."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Hashable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from typing import Generic, Literal, TypeVar


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(_handler)
T = TypeVar("T")
CacheSource = Literal["ninebot", "cache"]


@dataclass(frozen=True)
class CacheMetadata:
    source: CacheSource
    updated_at: datetime
    stale: bool


@dataclass(frozen=True)
class CacheResult(Generic[T]):
    value: T
    metadata: CacheMetadata


@dataclass(frozen=True)
class _CacheEntry(Generic[T]):
    value: T
    stored_at: float
    updated_at: datetime


class CacheService:
    """Cache successful values and use the newest stale value after failures."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[Hashable, _CacheEntry[object]] = {}
        self._locks: dict[Hashable, asyncio.Lock] = {}

    async def get_or_fetch(
        self,
        key: Hashable,
        ttl_seconds: float,
        fetcher: Callable[[], Awaitable[T]],
        *,
        endpoint: str,
        sn: str | None = None,
    ) -> T:
        """Compatibility wrapper returning only the cached or fetched value."""
        result = await self.get_or_fetch_with_metadata(
            key,
            ttl_seconds,
            fetcher,
            endpoint=endpoint,
            sn=sn,
        )
        return result.value

    async def get_or_fetch_with_metadata(
        self,
        key: Hashable,
        ttl_seconds: float,
        fetcher: Callable[[], Awaitable[T]],
        *,
        endpoint: str,
        sn: str | None = None,
    ) -> CacheResult[T]:
        """Return the value together with its source, age and stale state."""
        request_time = datetime.now(timezone.utc).isoformat()
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            entry = self._entries.get(key)
            now = self._clock()
            if entry is not None and now - entry.stored_at < ttl_seconds:
                metadata = CacheMetadata(
                    source="cache",
                    updated_at=entry.updated_at,
                    stale=False,
                )
                self._log(
                    request_time,
                    endpoint,
                    sn,
                    result="success",
                    source=metadata.source,
                    stale=metadata.stale,
                )
                return CacheResult(
                    value=deepcopy(entry.value),  # type: ignore[arg-type]
                    metadata=metadata,
                )

            try:
                value = await fetcher()
            except Exception:
                if entry is None:
                    self._log(
                        request_time,
                        endpoint,
                        sn,
                        result="failure",
                        source="ninebot",
                        stale=False,
                    )
                    raise
                metadata = CacheMetadata(
                    source="cache",
                    updated_at=entry.updated_at,
                    stale=True,
                )
                self._log(
                    request_time,
                    endpoint,
                    sn,
                    result="fallback",
                    source=metadata.source,
                    stale=metadata.stale,
                )
                return CacheResult(
                    value=deepcopy(entry.value),  # type: ignore[arg-type]
                    metadata=metadata,
                )

            updated_at = datetime.now(timezone.utc)
            self._entries[key] = _CacheEntry(
                value=deepcopy(value),
                stored_at=self._clock(),
                updated_at=updated_at,
            )
            metadata = CacheMetadata(
                source="ninebot",
                updated_at=updated_at,
                stale=False,
            )
            self._log(
                request_time,
                endpoint,
                sn,
                result="success",
                source=metadata.source,
                stale=metadata.stale,
            )
            return CacheResult(
                value=deepcopy(value),
                metadata=metadata,
            )

    @staticmethod
    def _log(
        request_time: str,
        endpoint: str,
        sn: str | None,
        *,
        result: str,
        source: CacheSource,
        stale: bool,
    ) -> None:
        fields = (
            f"request_time={request_time} "
            f"sn={sn or '-'} endpoint={endpoint} result={result} "
            f"source={source} stale={str(stale).lower()}"
        )
        if result == "success":
            LOGGER.info(fields)
        else:
            LOGGER.warning(fields)
