import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


SERVER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_ROOT))

from services.database_service import (  # noqa: E402
    DatabaseService,
    SCHEMA_VERSION,
)


class DatabaseServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "data" / "nineplus.sqlite3"
        self.service = DatabaseService(self.database_path)

    async def asyncTearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    async def test_initialize_creates_database_in_wal_mode(self) -> None:
        info = await self.service.initialize()

        self.assertTrue(self.database_path.is_file())
        self.assertEqual(info.path, self.database_path)
        self.assertEqual(info.schema_version, SCHEMA_VERSION)
        self.assertEqual(info.journal_mode, "wal")

        with closing(self._connection()) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")

    async def test_initialize_creates_required_tables_and_migration(self) -> None:
        await self.service.initialize()

        with closing(self._connection()) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            migrations = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertTrue(
            {"schema_migrations", "vehicles", "vehicle_snapshots", "collection_runs"}
            <= tables
        )
        self.assertEqual(migrations, [(SCHEMA_VERSION,)])

    async def test_vehicle_snapshots_has_required_columns_and_foreign_key(self) -> None:
        await self.service.initialize()

        with closing(self._connection()) as connection:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(vehicle_snapshots)")
            }
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(vehicle_snapshots)"
            ).fetchall()

        self.assertTrue(
            {
                "collected_at_ms",
                "sn",
                "battery_percent",
                "battery_voltage",
                "battery_temperature",
                "estimated_range_km",
                "odometer_km",
                "latitude",
                "longitude",
            }
            <= columns
        )
        self.assertTrue(
            any(row[2] == "vehicles" and row[3] == "sn" and row[4] == "sn" for row in foreign_keys)
        )

    async def test_initialize_creates_indexes_and_is_idempotent(self) -> None:
        await self.service.initialize()
        second_info = await self.service.initialize()

        with closing(self._connection()) as connection:
            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]

        self.assertEqual(second_info.schema_version, SCHEMA_VERSION)
        self.assertTrue(
            {
                "idx_vehicle_snapshots_sn_collected_at",
                "idx_vehicle_snapshots_collected_at",
                "idx_collection_runs_started_at",
            }
            <= indexes
        )
        self.assertEqual(migration_count, 1)

    async def test_concurrent_initialization_applies_migration_once(self) -> None:
        other_service = DatabaseService(self.database_path)

        first_info, second_info = await asyncio.gather(
            self.service.initialize(),
            other_service.initialize(),
        )

        with closing(self._connection()) as connection:
            migrations = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()

        self.assertEqual(first_info.schema_version, SCHEMA_VERSION)
        self.assertEqual(second_info.schema_version, SCHEMA_VERSION)
        self.assertEqual(migrations, [(SCHEMA_VERSION,)])


if __name__ == "__main__":
    unittest.main()
