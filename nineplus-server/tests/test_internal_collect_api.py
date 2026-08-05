from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from services.database_service import DatabaseService  # noqa: E402
from services.telemetry_collector import CollectionSummary  # noqa: E402


TEST_API_KEY = "internal-collect-test-key"


class FakeTelemetryCollector:
    def __init__(self, summary: CollectionSummary) -> None:
        self.summary = summary
        self.call_count = 0

    async def collect_once(self) -> CollectionSummary:
        self.call_count += 1
        return self.summary


class FakeTelemetryScheduler:
    async def start(self) -> bool:
        return False

    async def stop(self) -> bool:
        return False


class InternalCollectApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nineplus.sqlite3"
        self.database_service = DatabaseService(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _request(
        self,
        collector: FakeTelemetryCollector,
        *,
        authorization: str | None,
    ):
        headers = {"Authorization": authorization} if authorization else {}
        with (
            patch.object(main, "DATABASE_SERVICE", self.database_service),
            patch.object(main, "TELEMETRY_COLLECTOR", collector),
            patch.object(main, "TELEMETRY_SCHEDULER", FakeTelemetryScheduler()),
            patch.object(main, "NINEPLUS_API_KEY", TEST_API_KEY),
            TestClient(main.app) as client,
        ):
            return client.post("/internal/collect", headers=headers)

    def test_missing_api_key_is_rejected_without_collecting(self) -> None:
        collector = FakeTelemetryCollector(
            CollectionSummary(
                result="success",
                vehicle_count=1,
                inserted_count=1,
                error_codes=(),
            )
        )

        response = self._request(collector, authorization=None)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "nineplus_unauthorized")
        self.assertEqual(collector.call_count, 0)

    def test_valid_api_key_runs_collection_and_returns_success(self) -> None:
        collector = FakeTelemetryCollector(
            CollectionSummary(
                result="success",
                vehicle_count=1,
                inserted_count=1,
                error_codes=(),
            )
        )

        response = self._request(
            collector,
            authorization=f"Bearer {TEST_API_KEY}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "data": {
                    "result": "success",
                    "inserted_count": 1,
                    "success": True,
                },
            },
        )
        self.assertEqual(collector.call_count, 1)

    def test_collection_failure_is_reported_without_faking_success(self) -> None:
        collector = FakeTelemetryCollector(
            CollectionSummary(
                result="failure",
                vehicle_count=1,
                inserted_count=0,
                error_codes=("ninebot_status_unavailable",),
            )
        )

        response = self._request(
            collector,
            authorization=f"Bearer {TEST_API_KEY}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["result"], "failure")
        self.assertEqual(response.json()["data"]["inserted_count"], 0)
        self.assertFalse(response.json()["data"]["success"])
        self.assertEqual(collector.call_count, 1)


if __name__ == "__main__":
    unittest.main()
