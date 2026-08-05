import asyncio
from pathlib import Path
import sys
import unittest


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from services.telemetry_scheduler import (  # noqa: E402
    DEFAULT_COLLECTION_INTERVAL_SECONDS,
    TelemetryScheduler,
)


class CountingCollector:
    def __init__(self, *, failures: int = 0) -> None:
        self.call_count = 0
        self.failures = failures

    async def collect_once(self) -> object:
        self.call_count += 1
        if self.call_count <= self.failures:
            raise RuntimeError("simulated collector failure")
        return object()


async def wait_for_calls(collector: CountingCollector, expected: int) -> None:
    async def wait() -> None:
        while collector.call_count < expected:
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), timeout=1.0)


class TelemetrySchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_scheduler_starts_and_collects_immediately(self) -> None:
        collector = CountingCollector()
        scheduler = TelemetryScheduler(
            collector,
            enabled=True,
            interval_seconds=60,
        )

        started = await scheduler.start()
        await wait_for_calls(collector, 1)

        self.assertTrue(started)
        self.assertTrue(scheduler.is_running)
        await scheduler.stop()

    async def test_stop_cancels_background_task(self) -> None:
        collector = CountingCollector()
        scheduler = TelemetryScheduler(
            collector,
            enabled=True,
            interval_seconds=0.02,
        )
        await scheduler.start()
        await wait_for_calls(collector, 1)

        stopped = await scheduler.stop()
        calls_after_stop = collector.call_count
        await asyncio.sleep(0.04)

        self.assertTrue(stopped)
        self.assertFalse(scheduler.is_running)
        self.assertEqual(collector.call_count, calls_after_stop)

    async def test_environment_controls_enabled_state_and_interval(self) -> None:
        collector = CountingCollector()
        default_scheduler = TelemetryScheduler.from_environment(collector, {})
        configured_scheduler = TelemetryScheduler.from_environment(
            collector,
            {
                "NINEPLUS_HISTORY_ENABLED": "true",
                "NINEPLUS_COLLECTION_INTERVAL_SECONDS": "12.5",
            },
        )

        self.assertFalse(default_scheduler.enabled)
        self.assertEqual(
            default_scheduler.interval_seconds,
            DEFAULT_COLLECTION_INTERVAL_SECONDS,
        )
        self.assertFalse(await default_scheduler.start())
        self.assertFalse(default_scheduler.is_running)
        self.assertTrue(configured_scheduler.enabled)
        self.assertEqual(configured_scheduler.interval_seconds, 12.5)

    async def test_collector_exception_does_not_stop_scheduler(self) -> None:
        collector = CountingCollector(failures=1)
        scheduler = TelemetryScheduler(
            collector,
            enabled=True,
            interval_seconds=0.01,
        )

        await scheduler.start()
        await wait_for_calls(collector, 2)

        self.assertTrue(scheduler.is_running)
        self.assertEqual(collector.call_count, 2)
        await scheduler.stop()


if __name__ == "__main__":
    unittest.main()
