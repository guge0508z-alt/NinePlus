"""SQLite database initialization and schema migration support."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Final, Literal


SCHEMA_VERSION: Final = 1
DEFAULT_BUSY_TIMEOUT_MS: Final = 5_000


_INITIALIZATION_LOCKS_GUARD = threading.Lock()
_INITIALIZATION_LOCKS: dict[str, threading.Lock] = {}


def _initialization_lock_for(database_path: Path) -> threading.Lock:
    """Return one process-wide initialization lock for each SQLite file."""
    normalized_path = os.path.normcase(os.path.abspath(os.fspath(database_path)))
    with _INITIALIZATION_LOCKS_GUARD:
        lock = _INITIALIZATION_LOCKS.get(normalized_path)
        if lock is None:
            lock = threading.Lock()
            _INITIALIZATION_LOCKS[normalized_path] = lock
        return lock


def _default_database_path() -> Path:
    configured_path = os.getenv("NINEPLUS_DB_PATH", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    if os.name == "nt":
        return Path(__file__).resolve().parents[1] / "data" / "nineplus.sqlite3"
    return Path("/var/lib/nineplus-server/nineplus.sqlite3")


MIGRATIONS: Final[dict[int, tuple[str, ...]]] = {
    1: (
        """
        CREATE TABLE vehicles (
            sn TEXT PRIMARY KEY NOT NULL CHECK (length(trim(sn)) > 0),
            name TEXT,
            model TEXT,
            first_seen_at_ms INTEGER NOT NULL CHECK (first_seen_at_ms >= 0),
            last_seen_at_ms INTEGER NOT NULL CHECK (last_seen_at_ms >= first_seen_at_ms)
        )
        """,
        """
        CREATE TABLE vehicle_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sn TEXT NOT NULL REFERENCES vehicles(sn) ON DELETE CASCADE,
            collected_at_ms INTEGER NOT NULL CHECK (collected_at_ms >= 0),
            status_observed_at_ms INTEGER CHECK (
                status_observed_at_ms IS NULL OR status_observed_at_ms >= 0
            ),
            battery_observed_at_ms INTEGER CHECK (
                battery_observed_at_ms IS NULL OR battery_observed_at_ms >= 0
            ),
            battery_percent INTEGER CHECK (
                battery_percent IS NULL OR battery_percent BETWEEN 0 AND 100
            ),
            battery_voltage REAL CHECK (
                battery_voltage IS NULL OR battery_voltage >= 0
            ),
            battery_temperature REAL,
            estimated_range_km REAL CHECK (
                estimated_range_km IS NULL OR estimated_range_km >= 0
            ),
            odometer_km REAL CHECK (odometer_km IS NULL OR odometer_km >= 0),
            latitude REAL CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
            longitude REAL CHECK (
                longitude IS NULL OR longitude BETWEEN -180 AND 180
            ),
            status_available INTEGER NOT NULL DEFAULT 0 CHECK (
                status_available IN (0, 1)
            ),
            battery_available INTEGER NOT NULL DEFAULT 0 CHECK (
                battery_available IN (0, 1)
            ),
            status_source TEXT CHECK (status_source IN ('ninebot', 'cache')),
            battery_source TEXT CHECK (battery_source IN ('ninebot', 'cache')),
            status_stale INTEGER NOT NULL DEFAULT 0 CHECK (status_stale IN (0, 1)),
            battery_stale INTEGER NOT NULL DEFAULT 0 CHECK (
                battery_stale IN (0, 1)
            )
        )
        """,
        """
        CREATE TABLE collection_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
            finished_at_ms INTEGER CHECK (
                finished_at_ms IS NULL OR finished_at_ms >= started_at_ms
            ),
            result TEXT NOT NULL CHECK (
                result IN ('running', 'success', 'partial', 'failure')
            ),
            vehicle_count INTEGER NOT NULL DEFAULT 0 CHECK (vehicle_count >= 0),
            inserted_count INTEGER NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
            error_code TEXT
        )
        """,
        """
        CREATE INDEX idx_vehicle_snapshots_sn_collected_at
        ON vehicle_snapshots(sn, collected_at_ms DESC)
        """,
        """
        CREATE INDEX idx_vehicle_snapshots_collected_at
        ON vehicle_snapshots(collected_at_ms)
        """,
        """
        CREATE INDEX idx_collection_runs_started_at
        ON collection_runs(started_at_ms DESC)
        """,
    ),
}


class DatabaseInitializationError(RuntimeError):
    """Raised when SQLite cannot be initialized or migrated safely."""


class DatabaseWriteError(RuntimeError):
    """Raised when a telemetry transaction cannot be committed."""


class DatabaseReadError(RuntimeError):
    """Raised when persisted telemetry cannot be queried."""


@dataclass(frozen=True)
class DatabaseInfo:
    path: Path
    schema_version: int
    journal_mode: str


DataSource = Literal["ninebot", "cache"]
CollectionResult = Literal["running", "success", "partial", "failure"]


@dataclass(frozen=True)
class VehicleRecord:
    sn: str
    name: str | None
    model: str | None
    seen_at_ms: int


@dataclass(frozen=True)
class VehicleSnapshotRecord:
    sn: str
    collected_at_ms: int
    status_observed_at_ms: int | None = None
    battery_observed_at_ms: int | None = None
    battery_percent: int | None = None
    battery_voltage: float | None = None
    battery_temperature: float | None = None
    estimated_range_km: float | None = None
    odometer_km: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    status_available: bool = False
    battery_available: bool = False
    status_source: DataSource | None = None
    battery_source: DataSource | None = None
    status_stale: bool = False
    battery_stale: bool = False


@dataclass(frozen=True)
class CollectionRunRecord:
    started_at_ms: int
    finished_at_ms: int | None
    result: CollectionResult
    vehicle_count: int
    inserted_count: int
    error_code: str | None = None


@dataclass(frozen=True)
class VehicleHistoryRecord:
    collected_at_ms: int
    battery_percent: int | None
    battery_voltage: float | None
    battery_temperature: float | None
    estimated_range_km: float | None
    latitude: float | None
    longitude: float | None
    status_source: DataSource | None
    battery_source: DataSource | None
    status_stale: bool
    battery_stale: bool


@dataclass(frozen=True)
class VehicleHistoryResult:
    vehicle_exists: bool
    snapshots: tuple[VehicleHistoryRecord, ...]


class DatabaseService:
    """Create and migrate the persistent NinePlus SQLite database."""

    def __init__(
        self,
        database_path: str | Path | None = None,
        *,
        busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    ) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self._database_path = Path(database_path or _default_database_path()).expanduser()
        self._busy_timeout_ms = busy_timeout_ms
        self._initialize_lock = asyncio.Lock()
        self._path_initialization_lock = _initialization_lock_for(self._database_path)

    @property
    def database_path(self) -> Path:
        return self._database_path

    async def initialize(self) -> DatabaseInfo:
        """Create the database and apply every pending migration once."""
        async with self._initialize_lock:
            return await asyncio.to_thread(self._initialize_with_path_lock)

    def _initialize_with_path_lock(self) -> DatabaseInfo:
        with self._path_initialization_lock:
            return self._initialize_sync()

    async def save_collection(
        self,
        vehicles: list[VehicleRecord],
        snapshots: list[VehicleSnapshotRecord],
        run: CollectionRunRecord,
    ) -> None:
        """Atomically persist normalized vehicles, snapshots and run metadata."""
        await asyncio.to_thread(
            self._save_collection_sync,
            tuple(vehicles),
            tuple(snapshots),
            run,
        )

    async def query_vehicle_history(
        self,
        sn: str,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 288,
    ) -> VehicleHistoryResult:
        """Return the newest matching snapshots in chronological order."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise ValueError("vehicle SN is empty")
        if not 1 <= limit <= 1_000:
            raise ValueError("history limit must be between 1 and 1000")
        if from_ms is not None and to_ms is not None and from_ms > to_ms:
            raise ValueError("history from time must not be after to time")
        return await asyncio.to_thread(
            self._query_vehicle_history_sync,
            normalized_sn,
            from_ms,
            to_ms,
            limit,
        )

    def _initialize_sync(self) -> DatabaseInfo:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            journal_mode_row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            journal_mode = str(journal_mode_row[0]).lower() if journal_mode_row else ""
            if journal_mode != "wal":
                raise DatabaseInitializationError(
                    f"SQLite WAL mode is unavailable (journal_mode={journal_mode or 'unknown'})"
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at_ms INTEGER NOT NULL CHECK (applied_at_ms >= 0)
                )
                """
            )
            connection.commit()

            try:
                connection.execute("BEGIN IMMEDIATE")
                current_version = self._current_schema_version(connection)
                if current_version > SCHEMA_VERSION:
                    raise DatabaseInitializationError(
                        "Database schema is newer than this NinePlus Server version"
                    )

                for version in range(current_version + 1, SCHEMA_VERSION + 1):
                    statements = MIGRATIONS.get(version)
                    if not statements:
                        raise DatabaseInitializationError(
                            f"Database migration {version} is missing"
                        )
                    for statement in statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at_ms) VALUES (?, ?)",
                        (version, int(time.time() * 1_000)),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

            schema_version = self._current_schema_version(connection)
        except (sqlite3.Error, OSError) as error:
            raise DatabaseInitializationError("Could not initialize SQLite database") from error
        finally:
            connection.close()

        self._restrict_permissions()
        return DatabaseInfo(
            path=self._database_path,
            schema_version=schema_version,
            journal_mode=journal_mode,
        )

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._busy_timeout_ms / 1_000,
            )
            connection.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=NORMAL")
            return connection
        except sqlite3.Error as error:
            raise DatabaseInitializationError("Could not open SQLite database") from error

    def _save_collection_sync(
        self,
        vehicles: tuple[VehicleRecord, ...],
        snapshots: tuple[VehicleSnapshotRecord, ...],
        run: CollectionRunRecord,
    ) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for vehicle in vehicles:
                connection.execute(
                    """
                    INSERT INTO vehicles(sn, name, model, first_seen_at_ms, last_seen_at_ms)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(sn) DO UPDATE SET
                        name = excluded.name,
                        model = excluded.model,
                        first_seen_at_ms = MIN(vehicles.first_seen_at_ms, excluded.first_seen_at_ms),
                        last_seen_at_ms = MAX(vehicles.last_seen_at_ms, excluded.last_seen_at_ms)
                    """,
                    (
                        vehicle.sn,
                        vehicle.name,
                        vehicle.model,
                        vehicle.seen_at_ms,
                        vehicle.seen_at_ms,
                    ),
                )

            for snapshot in snapshots:
                connection.execute(
                    """
                    INSERT INTO vehicle_snapshots(
                        sn,
                        collected_at_ms,
                        status_observed_at_ms,
                        battery_observed_at_ms,
                        battery_percent,
                        battery_voltage,
                        battery_temperature,
                        estimated_range_km,
                        odometer_km,
                        latitude,
                        longitude,
                        status_available,
                        battery_available,
                        status_source,
                        battery_source,
                        status_stale,
                        battery_stale
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.sn,
                        snapshot.collected_at_ms,
                        snapshot.status_observed_at_ms,
                        snapshot.battery_observed_at_ms,
                        snapshot.battery_percent,
                        snapshot.battery_voltage,
                        snapshot.battery_temperature,
                        snapshot.estimated_range_km,
                        snapshot.odometer_km,
                        snapshot.latitude,
                        snapshot.longitude,
                        int(snapshot.status_available),
                        int(snapshot.battery_available),
                        snapshot.status_source,
                        snapshot.battery_source,
                        int(snapshot.status_stale),
                        int(snapshot.battery_stale),
                    ),
                )

            connection.execute(
                """
                INSERT INTO collection_runs(
                    started_at_ms,
                    finished_at_ms,
                    result,
                    vehicle_count,
                    inserted_count,
                    error_code
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run.started_at_ms,
                    run.finished_at_ms,
                    run.result,
                    run.vehicle_count,
                    run.inserted_count,
                    run.error_code,
                ),
            )
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise DatabaseWriteError("Could not save telemetry collection") from error
        finally:
            connection.close()

    def _query_vehicle_history_sync(
        self,
        sn: str,
        from_ms: int | None,
        to_ms: int | None,
        limit: int,
    ) -> VehicleHistoryResult:
        connection = self._connect()
        connection.row_factory = sqlite3.Row
        try:
            vehicle_exists = (
                connection.execute(
                    "SELECT 1 FROM vehicles WHERE sn = ? LIMIT 1",
                    (sn,),
                ).fetchone()
                is not None
            )
            if not vehicle_exists:
                return VehicleHistoryResult(vehicle_exists=False, snapshots=())

            rows = connection.execute(
                """
                WITH recent AS (
                    SELECT
                        id,
                        collected_at_ms,
                        battery_percent,
                        battery_voltage,
                        battery_temperature,
                        estimated_range_km,
                        latitude,
                        longitude,
                        status_source,
                        battery_source,
                        status_stale,
                        battery_stale
                    FROM vehicle_snapshots
                    WHERE sn = ?
                      AND (? IS NULL OR collected_at_ms >= ?)
                      AND (? IS NULL OR collected_at_ms <= ?)
                    ORDER BY collected_at_ms DESC, id DESC
                    LIMIT ?
                )
                SELECT * FROM recent
                ORDER BY collected_at_ms ASC, id ASC
                """,
                (sn, from_ms, from_ms, to_ms, to_ms, limit),
            ).fetchall()
            snapshots = tuple(
                VehicleHistoryRecord(
                    collected_at_ms=int(row["collected_at_ms"]),
                    battery_percent=row["battery_percent"],
                    battery_voltage=row["battery_voltage"],
                    battery_temperature=row["battery_temperature"],
                    estimated_range_km=row["estimated_range_km"],
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    status_source=row["status_source"],
                    battery_source=row["battery_source"],
                    status_stale=bool(row["status_stale"]),
                    battery_stale=bool(row["battery_stale"]),
                )
                for row in rows
            )
            return VehicleHistoryResult(
                vehicle_exists=True,
                snapshots=snapshots,
            )
        except sqlite3.Error as error:
            raise DatabaseReadError("Could not query vehicle history") from error
        finally:
            connection.close()

    @staticmethod
    def _current_schema_version(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        return int(row[0]) if row else 0

    def _restrict_permissions(self) -> None:
        """Use private permissions where the host filesystem supports them."""
        try:
            self._database_path.parent.chmod(0o700)
            self._database_path.chmod(0o600)
        except OSError:
            # Windows and some mounted filesystems do not implement POSIX modes.
            pass
