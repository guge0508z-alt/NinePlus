from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from services.cache_service import CacheMetadata, CacheResult  # noqa: E402
from services.database_service import DatabaseService  # noqa: E402
from services.ninebot_service import NinebotServiceError  # noqa: E402
from services.telemetry_collector import TelemetryCollector  # noqa: E402


FIXED_TIME = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
TEST_SN = "0PDTESTCOLLECTOR695"


def cache_result(value):
    return CacheResult(
        value=value,
        metadata=CacheMetadata(
            source="ninebot",
            updated_at=FIXED_TIME,
            stale=False,
        ),
    )


class FakeNinebotService:
    def __init__(self, *, status_fails: bool = False, battery_fails: bool = False):
        self.status_fails = status_fails
        self.battery_fails = battery_fails

    async def fetch_vehicles_with_metadata(self):
        return cache_result(
            [
                {
                    "wnumber": TEST_SN,
                    "vehicle_name": "NX MIX",
                    "device_name": "我的 NX MIX",
                    "vehicle_name_en": "NX MIX",
                }
            ]
        )

    async def get_dashboard_with_metadata(self, sn: str):
        if self.status_fails:
            raise NinebotServiceError("simulated status failure")
        return cache_result(
            {
                "sn": sn,
                "dump_energy": "77",
                "precise_estimate_mileage": "55.0",
                "charging": 0,
                "pwr": 0,
                "loc": {"lat": "31.2304", "lon": "121.4737", "lock": 1},
            }
        )

    async def get_battery_with_metadata(self, sn: str):
        if self.battery_fails:
            raise NinebotServiceError("simulated battery failure")
        return cache_result(
            {
                "electricity": 77,
                "battery_voltage": 76.2,
                "battery_temperature": 31,
                "bms_cycle": 100,
                "charging": 0,
            }
        )


class TelemetryCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nineplus.sqlite3"
        self.database = DatabaseService(self.database_path)
        await self.database.initialize()

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _snapshot(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM vehicle_snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def _run(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def _vehicle(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(
                "SELECT * FROM vehicles WHERE sn = ?",
                (TEST_SN,),
            ).fetchone()

    async def test_collect_once_writes_normalized_vehicle_snapshot(self) -> None:
        collector = TelemetryCollector(
            self.database,
            FakeNinebotService(),
            clock=lambda: FIXED_TIME,
        )

        summary = await collector.collect_once()
        snapshot = self._snapshot()
        run = self._run()
        vehicle = self._vehicle()

        self.assertEqual(summary.result, "success")
        self.assertEqual(summary.inserted_count, 1)
        self.assertEqual(vehicle["sn"], TEST_SN)
        self.assertEqual(vehicle["name"], "NX MIX")
        self.assertEqual(vehicle["model"], "NX MIX")
        self.assertEqual(snapshot["sn"], TEST_SN)
        self.assertEqual(snapshot["battery_percent"], 77)
        self.assertAlmostEqual(snapshot["battery_voltage"], 76.2)
        self.assertAlmostEqual(snapshot["battery_temperature"], 31.0)
        self.assertAlmostEqual(snapshot["estimated_range_km"], 55.0)
        self.assertAlmostEqual(snapshot["latitude"], 31.2304)
        self.assertAlmostEqual(snapshot["longitude"], 121.4737)
        self.assertEqual(snapshot["status_available"], 1)
        self.assertEqual(snapshot["battery_available"], 1)
        self.assertEqual(snapshot["status_source"], "ninebot")
        self.assertEqual(snapshot["battery_source"], "ninebot")
        self.assertEqual(run["result"], "success")

    async def test_status_failure_still_writes_battery_snapshot(self) -> None:
        collector = TelemetryCollector(
            self.database,
            FakeNinebotService(status_fails=True),
            clock=lambda: FIXED_TIME,
        )

        summary = await collector.collect_once()
        snapshot = self._snapshot()

        self.assertEqual(summary.result, "partial")
        self.assertEqual(summary.error_codes, ("ninebot_status_unavailable",))
        self.assertEqual(snapshot["status_available"], 0)
        self.assertEqual(snapshot["battery_available"], 1)
        self.assertIsNone(snapshot["estimated_range_km"])
        self.assertIsNone(snapshot["latitude"])
        self.assertEqual(snapshot["battery_percent"], 77)
        self.assertAlmostEqual(snapshot["battery_voltage"], 76.2)

    async def test_battery_failure_still_writes_status_snapshot(self) -> None:
        collector = TelemetryCollector(
            self.database,
            FakeNinebotService(battery_fails=True),
            clock=lambda: FIXED_TIME,
        )

        summary = await collector.collect_once()
        snapshot = self._snapshot()

        self.assertEqual(summary.result, "partial")
        self.assertEqual(summary.error_codes, ("ninebot_battery_unavailable",))
        self.assertEqual(snapshot["status_available"], 1)
        self.assertEqual(snapshot["battery_available"], 0)
        self.assertEqual(snapshot["battery_percent"], 77)
        self.assertIsNone(snapshot["battery_voltage"])
        self.assertIsNone(snapshot["battery_temperature"])
        self.assertAlmostEqual(snapshot["estimated_range_km"], 55.0)
        self.assertAlmostEqual(snapshot["latitude"], 31.2304)


if __name__ == "__main__":
    unittest.main()
