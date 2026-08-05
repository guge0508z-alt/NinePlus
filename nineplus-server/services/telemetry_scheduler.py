"""Optional background scheduling for NinePlus telemetry collection."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import os
from typing import Protocol


LOGGER = logging.getLogger(__name__)
DEFAULT_COLLECTION_INTERVAL_SECONDS = 300.0
_ENABLED_VALUES = {"1", "true", "yes", "on"}


class ScheduledCollector(Protocol):
    async def collect_once(self) -> object: ...


class TelemetryScheduler:
    """Run one collector repeatedly without exposing additional API behavior."""

    def __init__(
        self,
        collector: ScheduledCollector,
        *,
        enabled: bool = False,
        interval_seconds: float = DEFAULT_COLLECTION_INTERVAL_SECONDS,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("collection interval must be positive")
        self._collector = collector
        self._enabled = enabled
        self._interval_seconds = float(interval_seconds)
        self._task: asyncio.Task[None] | None = None

    @classmethod
    def from_environment(
        cls,
        collector: ScheduledCollector,
        environment: Mapping[str, str] | None = None,
    ) -> "TelemetryScheduler":
        values = os.environ if environment is None else environment
        enabled = values.get("NINEPLUS_HISTORY_ENABLED", "false").strip().lower()
        interval_text = values.get(
            "NINEPLUS_COLLECTION_INTERVAL_SECONDS",
            str(DEFAULT_COLLECTION_INTERVAL_SECONDS),
        ).strip()
        try:
            interval_seconds = float(interval_text)
        except ValueError as error:
            raise ValueError(
                "NINEPLUS_COLLECTION_INTERVAL_SECONDS must be a number"
            ) from error
        return cls(
            collector,
            enabled=enabled in _ENABLED_VALUES,
            interval_seconds=interval_seconds,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def interval_seconds(self) -> float:
        return self._interval_seconds

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> bool:
        """Start the scheduler once; return whether a task was created."""
        if not self._enabled or self.is_running:
            return False
        self._task = asyncio.create_task(
            self._run(),
            name="nineplus-telemetry-scheduler",
        )
        LOGGER.info(
            "telemetry scheduler started interval_seconds=%s",
            self._interval_seconds,
        )
        return True

    async def stop(self) -> bool:
        """Cancel and await the task so shutdown leaves no background work."""
        task = self._task
        if task is None:
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
        LOGGER.info("telemetry scheduler stopped")
        return True

    async def _run(self) -> None:
        while True:
            try:
                await self._collector.collect_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                LOGGER.warning(
                    "telemetry scheduler collection failed error=%s",
                    type(error).__name__,
                )
            await asyncio.sleep(self._interval_seconds)
