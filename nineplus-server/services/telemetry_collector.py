"""Single-run collection of normalized Ninebot vehicle telemetry."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Protocol

from adapters.ninebot_mapper import (
    NinebotMappingError,
    map_ninebot_battery,
    map_ninebot_dashboard,
    map_ninebot_vehicles,
)
from services.cache_service import CacheResult
from services.database_service import (
    CollectionResult,
    CollectionRunRecord,
    DatabaseService,
    VehicleRecord,
    VehicleSnapshotRecord,
)
from services.ninebot_service import NinebotService, NinebotServiceError


LOGGER = logging.getLogger(__name__)


class TelemetryNinebotService(Protocol):
    async def fetch_vehicles_with_metadata(
        self,
    ) -> CacheResult[list[dict[str, Any]]]: ...

    async def get_dashboard_with_metadata(
        self,
        sn: str,
    ) -> CacheResult[dict[str, Any]]: ...

    async def get_battery_with_metadata(
        self,
        sn: str,
    ) -> CacheResult[dict[str, Any]]: ...


@dataclass(frozen=True)
class CollectionSummary:
    result: CollectionResult
    vehicle_count: int
    inserted_count: int
    error_codes: tuple[str, ...]


class TelemetryCollector:
    """Collect one snapshot without scheduling or exposing an HTTP endpoint."""

    def __init__(
        self,
        database_service: DatabaseService,
        ninebot_service: TelemetryNinebotService | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database_service
        self._ninebot = ninebot_service or NinebotService()
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def collect_once(self) -> CollectionSummary:
        """Read vehicles, status and battery once, then persist normalized values."""
        started_at_ms = self._now_ms()
        try:
            vehicles_result = await self._ninebot.fetch_vehicles_with_metadata()
            mapped_vehicles = map_ninebot_vehicles(vehicles_result.value)
        except (NinebotServiceError, NinebotMappingError, ValueError):
            return await self._finish_failed_vehicle_collection(started_at_ms)

        collected_at_ms = self._now_ms()
        vehicle_records = [
            VehicleRecord(
                sn=vehicle["wnumber"],
                name=vehicle.get("device_name"),
                model=vehicle.get("vehicle_name_en"),
                seen_at_ms=collected_at_ms,
            )
            for vehicle in mapped_vehicles
            if vehicle["wnumber"] != "TEST000001"
        ]

        snapshots: list[VehicleSnapshotRecord] = []
        error_codes: list[str] = []
        for vehicle in vehicle_records:
            snapshot, vehicle_errors = await self._collect_vehicle(
                vehicle.sn,
                collected_at_ms,
            )
            error_codes.extend(vehicle_errors)
            if snapshot is not None:
                snapshots.append(snapshot)

        unique_errors = tuple(sorted(set(error_codes)))
        if not vehicle_records or not snapshots:
            result: CollectionResult = "failure"
        elif unique_errors:
            result = "partial"
        else:
            result = "success"

        run = CollectionRunRecord(
            started_at_ms=started_at_ms,
            finished_at_ms=self._now_ms(),
            result=result,
            vehicle_count=len(vehicle_records),
            inserted_count=len(snapshots),
            error_code=",".join(unique_errors) or None,
        )
        await self._database.save_collection(vehicle_records, snapshots, run)
        self._log_summary(run)
        return CollectionSummary(
            result=result,
            vehicle_count=len(vehicle_records),
            inserted_count=len(snapshots),
            error_codes=unique_errors,
        )

    async def _collect_vehicle(
        self,
        sn: str,
        collected_at_ms: int,
    ) -> tuple[VehicleSnapshotRecord | None, list[str]]:
        status_result, battery_result = await asyncio.gather(
            self._read_status(sn),
            self._read_battery(sn),
        )
        errors: list[str] = []
        mapped_state: dict[str, Any] | None = None
        mapped_battery: dict[str, Any] | None = None

        if status_result is None:
            errors.append("ninebot_status_unavailable")
        else:
            try:
                mapped_state = map_ninebot_dashboard(status_result.value, sn)["state"]
            except (NinebotMappingError, TypeError, ValueError, KeyError):
                status_result = None
                errors.append("ninebot_status_invalid")

        if battery_result is None:
            errors.append("ninebot_battery_unavailable")
        else:
            try:
                mapped_battery = map_ninebot_battery(battery_result.value)
            except (NinebotMappingError, TypeError, ValueError, KeyError):
                battery_result = None
                errors.append("ninebot_battery_invalid")

        if mapped_state is None and mapped_battery is None:
            return None, errors

        location = mapped_state.get("loc") if mapped_state else None
        location = location if isinstance(location, dict) else {}
        battery_percent = (
            mapped_battery.get("electricity")
            if mapped_battery is not None
            else mapped_state.get("dump_energy") if mapped_state is not None else None
        )
        return (
            VehicleSnapshotRecord(
                sn=sn,
                collected_at_ms=collected_at_ms,
                status_observed_at_ms=(
                    self._datetime_ms(status_result.metadata.updated_at)
                    if status_result is not None
                    else None
                ),
                battery_observed_at_ms=(
                    self._datetime_ms(battery_result.metadata.updated_at)
                    if battery_result is not None
                    else None
                ),
                battery_percent=battery_percent,
                battery_voltage=(
                    mapped_battery.get("battery_voltage")
                    if mapped_battery is not None
                    else None
                ),
                battery_temperature=(
                    mapped_battery.get("battery_temperature")
                    if mapped_battery is not None
                    else None
                ),
                estimated_range_km=(
                    mapped_state.get("estimate_mileage")
                    if mapped_state is not None
                    else None
                ),
                latitude=location.get("lat"),
                longitude=location.get("lon"),
                status_available=status_result is not None,
                battery_available=battery_result is not None,
                status_source=(
                    status_result.metadata.source if status_result is not None else None
                ),
                battery_source=(
                    battery_result.metadata.source if battery_result is not None else None
                ),
                status_stale=(
                    status_result.metadata.stale if status_result is not None else False
                ),
                battery_stale=(
                    battery_result.metadata.stale if battery_result is not None else False
                ),
            ),
            errors,
        )

    async def _read_status(self, sn: str) -> CacheResult[dict[str, Any]] | None:
        try:
            return await self._ninebot.get_dashboard_with_metadata(sn)
        except (NinebotServiceError, ValueError):
            return None

    async def _read_battery(self, sn: str) -> CacheResult[dict[str, Any]] | None:
        try:
            return await self._ninebot.get_battery_with_metadata(sn)
        except (NinebotServiceError, ValueError):
            return None

    async def _finish_failed_vehicle_collection(
        self,
        started_at_ms: int,
    ) -> CollectionSummary:
        error_code = "ninebot_vehicles_unavailable"
        run = CollectionRunRecord(
            started_at_ms=started_at_ms,
            finished_at_ms=self._now_ms(),
            result="failure",
            vehicle_count=0,
            inserted_count=0,
            error_code=error_code,
        )
        await self._database.save_collection([], [], run)
        self._log_summary(run)
        return CollectionSummary(
            result="failure",
            vehicle_count=0,
            inserted_count=0,
            error_codes=(error_code,),
        )

    def _now_ms(self) -> int:
        return self._datetime_ms(self._clock())

    @staticmethod
    def _datetime_ms(value: datetime) -> int:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1_000)

    @staticmethod
    def _log_summary(run: CollectionRunRecord) -> None:
        LOGGER.info(
            "telemetry collection result=%s vehicles=%s inserted=%s error_code=%s",
            run.result,
            run.vehicle_count,
            run.inserted_count,
            run.error_code or "-",
        )
