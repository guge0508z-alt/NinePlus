import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from services.database_service import (  # noqa: E402
    CollectionRunRecord,
    DatabaseService,
    VehicleRecord,
    VehicleSnapshotRecord,
)


TEST_API_KEY = "history-api-test-key"
TEST_SN = "0PDHISTORYTEST695"
EMPTY_SN = "0PDHISTORYEMPTY695"


def milliseconds(hour: int) -> int:
    return int(datetime(2026, 8, 3, hour, tzinfo=timezone.utc).timestamp() * 1_000)


class FakeTelemetryScheduler:
    async def start(self) -> bool:
        return False

    async def stop(self) -> bool:
        return False


class HistoryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nineplus.sqlite3"
        self.database_service = DatabaseService(self.database_path)
        asyncio.run(self.database_service.initialize())
        asyncio.run(
            self.database_service.save_collection(
                [
                    VehicleRecord(TEST_SN, "NX MIX", "NX MIX", milliseconds(10)),
                    VehicleRecord(EMPTY_SN, "Empty Vehicle", "Test", milliseconds(10)),
                ],
                [
                    VehicleSnapshotRecord(
                        sn=TEST_SN,
                        collected_at_ms=milliseconds(10),
                        battery_percent=80,
                        battery_voltage=76.8,
                        battery_temperature=30.0,
                        estimated_range_km=60.0,
                        latitude=31.2304,
                        longitude=121.4737,
                        status_available=True,
                        battery_available=True,
                        status_source="ninebot",
                        battery_source="ninebot",
                    ),
                    VehicleSnapshotRecord(
                        sn=TEST_SN,
                        collected_at_ms=milliseconds(11),
                        battery_percent=75,
                        battery_voltage=76.2,
                        battery_temperature=31.0,
                        estimated_range_km=55.0,
                        latitude=31.2310,
                        longitude=121.4740,
                        status_available=True,
                        battery_available=True,
                        status_source="ninebot",
                        battery_source="cache",
                        battery_stale=True,
                    ),
                    VehicleSnapshotRecord(
                        sn=TEST_SN,
                        collected_at_ms=milliseconds(12),
                        battery_percent=70,
                        battery_voltage=75.9,
                        battery_temperature=32.0,
                        estimated_range_km=50.0,
                        status_available=True,
                        battery_available=True,
                        status_source="ninebot",
                        battery_source="ninebot",
                    ),
                ],
                CollectionRunRecord(
                    started_at_ms=milliseconds(10),
                    finished_at_ms=milliseconds(12),
                    result="success",
                    vehicle_count=2,
                    inserted_count=3,
                ),
            )
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _get(self, sn: str, *, params=None, authorized: bool = True):
        headers = (
            {"Authorization": f"Bearer {TEST_API_KEY}"}
            if authorized
            else {}
        )
        with (
            patch.object(main, "DATABASE_SERVICE", self.database_service),
            patch.object(main, "TELEMETRY_SCHEDULER", FakeTelemetryScheduler()),
            patch.object(main, "NINEPLUS_API_KEY", TEST_API_KEY),
            TestClient(main.app) as client,
        ):
            return client.get(
                f"/vehicles/{sn}/history",
                params=params,
                headers=headers,
            )

    def test_normal_query_returns_snapshots_and_aggregated_metadata(self) -> None:
        response = self._get(TEST_SN)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["sn"], TEST_SN)
        self.assertEqual(len(payload["data"]["list"]), 3)
        first, second, third = payload["data"]["list"]
        self.assertEqual(first["battery_percent"], 80)
        self.assertEqual(first["battery_voltage"], 76.8)
        self.assertEqual(first["battery_temperature"], 30.0)
        self.assertEqual(first["estimated_range_km"], 60.0)
        self.assertEqual(
            first["location"],
            {"latitude": 31.2304, "longitude": 121.4737},
        )
        self.assertEqual(first["source"], "ninebot")
        self.assertFalse(first["stale"])
        self.assertEqual(second["source"], "cache")
        self.assertTrue(second["stale"])
        self.assertIsNone(third["location"])

    def test_unknown_sn_returns_404(self) -> None:
        response = self._get("UNKNOWN-SN")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "vehicle_not_found")

    def test_time_range_filters_snapshots_inclusively(self) -> None:
        response = self._get(
            TEST_SN,
            params={
                "from": "2026-08-03T10:30:00Z",
                "to": "2026-08-03T11:30:00Z",
            },
        )

        self.assertEqual(response.status_code, 200)
        snapshots = response.json()["data"]["list"]
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["battery_percent"], 75)

    def test_limit_returns_latest_records_and_enforces_maximum(self) -> None:
        response = self._get(TEST_SN, params={"limit": 2})
        invalid_response = self._get(TEST_SN, params={"limit": 1_001})

        self.assertEqual(response.status_code, 200)
        snapshots = response.json()["data"]["list"]
        self.assertEqual([item["battery_percent"] for item in snapshots], [75, 70])
        self.assertEqual(invalid_response.status_code, 422)

    def test_known_vehicle_without_snapshots_returns_empty_list(self) -> None:
        response = self._get(EMPTY_SN)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["list"], [])

    def test_history_requires_existing_bearer_api_key(self) -> None:
        response = self._get(TEST_SN, authorized=False)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "nineplus_unauthorized")


if __name__ == "__main__":
    unittest.main()
