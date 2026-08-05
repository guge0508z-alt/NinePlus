from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

import main  # noqa: E402
from services.database_service import DatabaseService, SCHEMA_VERSION  # noqa: E402


class FakeTelemetryScheduler:
    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> bool:
        self.start_count += 1
        return True

    async def stop(self) -> bool:
        self.stop_count += 1
        return True


class DatabaseLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "nineplus.sqlite3"

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    async def test_app_lifespan_creates_database_before_serving(self) -> None:
        database_service = DatabaseService(self.database_path)
        scheduler = FakeTelemetryScheduler()

        with (
            patch.object(main, "DATABASE_SERVICE", database_service),
            patch.object(main, "TELEMETRY_SCHEDULER", scheduler),
        ):
            async with main.app.router.lifespan_context(main.app):
                self.assertTrue(self.database_path.is_file())
                self.assertIs(main.app.state.database_service, database_service)
                self.assertIs(main.app.state.telemetry_scheduler, scheduler)
                self.assertEqual(scheduler.start_count, 1)
                self.assertEqual(scheduler.stop_count, 0)
                self.assertEqual(
                    main.app.state.database_info.schema_version,
                    SCHEMA_VERSION,
                )
                self.assertEqual(main.app.state.database_info.journal_mode, "wal")
            self.assertEqual(scheduler.stop_count, 1)

        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("vehicle_snapshots", tables)

    async def test_systemd_entrypoint_uses_lifespan_with_writable_database_path(self) -> None:
        unit = (SERVER_ROOT / "nineplus-server.service").read_text(encoding="utf-8")

        self.assertIn("-m uvicorn main:app", unit)
        self.assertIn("ReadWritePaths=/var/lib/nineplus-server", unit)
        self.assertEqual(
            DatabaseService().database_path,
            (
                SERVER_ROOT / "data" / "nineplus.sqlite3"
                if sys.platform == "win32"
                else Path("/var/lib/nineplus-server/nineplus.sqlite3")
            ),
        )


if __name__ == "__main__":
    unittest.main()
