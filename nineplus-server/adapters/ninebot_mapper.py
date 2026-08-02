"""Map decrypted Ninebot vehicle fields to the current NinePlus contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any


class NinebotMappingError(ValueError):
    """Raised when Ninebot data cannot be represented as a NinePlus vehicle."""


NINEBOT_TIMEZONE = timezone(timedelta(hours=8))


def map_ninebot_vehicle(vehicle: dict[str, Any]) -> dict[str, str]:
    """Convert one ninecli vehicle record without changing the iOS contract."""
    sn = _nonempty_string(vehicle.get("wnumber"))
    if sn is None:
        raise NinebotMappingError("Ninebot vehicle is missing wnumber")

    name = (
        _nonempty_string(vehicle.get("vehicle_name"))
        or _nonempty_string(vehicle.get("device_name"))
        or sn
    )
    model = (
        _nonempty_string(vehicle.get("vehicle_name_en"))
        or _nonempty_string(vehicle.get("vehicle_name"))
        or name
    )
    mapped = {
        "wnumber": sn,
        "device_name": name,
        "vehicle_name_en": model,
    }
    mapped.update(_vehicle_image_fields(vehicle))
    return mapped


def map_ninebot_vehicles(vehicles: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Map every valid vehicle and fail if no record can be represented."""
    mapped: list[dict[str, str]] = []
    for vehicle in vehicles:
        try:
            mapped.append(map_ninebot_vehicle(vehicle))
        except NinebotMappingError:
            continue
    if not mapped:
        raise NinebotMappingError("Ninebot response contains no mappable vehicles")
    return mapped


def map_ninebot_dashboard(status: dict[str, Any], sn: str) -> dict[str, Any]:
    """Convert one ninecli status response to the current Dashboard contract."""
    normalized_sn = _nonempty_string(status.get("sn")) or _nonempty_string(sn)
    if normalized_sn is None:
        raise NinebotMappingError("Ninebot dashboard is missing a vehicle SN")

    location = status.get("loc")
    location = location if isinstance(location, dict) else {}

    battery_percent = _int_value(status.get("dump_energy"), default=0)
    charging = _int_value(status.get("charging"), default=0)
    lock_status = _int_value(
        location.get("lock"),
        default=_int_value(status.get("barrel_lock_status"), default=0),
    )
    estimate_mileage = _first_float(
        status.get("precise_estimate_mileage"),
        status.get("estimate_mileage"),
        status.get("ai_estimate_mileage"),
        default=0.0,
    )
    latitude = _float_value(location.get("lat"), default=0.0)
    longitude = _float_value(location.get("lon"), default=0.0)

    vehicle = status.get("_vehicle")
    vehicle = vehicle if isinstance(vehicle, dict) else {}
    device_name = (
        _nonempty_string(vehicle.get("vehicle_name"))
        or _nonempty_string(vehicle.get("device_name"))
        or _nonempty_string(status.get("ble_name"))
        or normalized_sn
    )
    model = (
        _nonempty_string(vehicle.get("vehicle_name_en"))
        or _nonempty_string(vehicle.get("vehicle_name"))
        or device_name
    )
    mapped_vehicle = {
        "wnumber": normalized_sn,
        "device_name": device_name,
        "vehicle_name_en": model,
    }
    mapped_vehicle.update(_vehicle_image_fields(vehicle))
    return {
        "vehicle": mapped_vehicle,
        "state": {
            "dump_energy": battery_percent,
            "estimate_mileage": estimate_mileage,
            "charging": charging,
            "pwr": _int_value(status.get("pwr"), default=0),
            "lock_status": lock_status,
            "loc": {
                "lat": latitude,
                "lon": longitude,
                "lock": lock_status,
            },
        },
        "battery": {
            "electricity": battery_percent,
            "battery_voltage": 0.0,
            "battery_temperature": 0.0,
            "bms_cycle": 0,
            "charging_power": 0.0,
            "charging": charging,
            "remain_charge_time": _float_value(
                status.get("remain_charge_time"),
                default=0.0,
            ),
        },
    }


def map_ninebot_travel(payload: dict[str, Any], month: str) -> dict[str, Any]:
    """Convert a real travel-list2 month to the current TravelResponse data."""
    raw_records = payload.get("list")
    raw_records = raw_records if isinstance(raw_records, list) else []
    records: list[dict[str, Any]] = []
    for raw_record in raw_records:
        if not isinstance(raw_record, dict):
            continue
        try:
            records.append(_map_travel_record(raw_record))
        except NinebotMappingError:
            continue

    raw_detail = payload.get("detail")
    raw_detail = raw_detail if isinstance(raw_detail, list) else []
    detail = [_float_value(value, default=0.0) for value in raw_detail]
    resolved_month = _nonempty_string(payload.get("month")) or month
    return {
        "month": resolved_month,
        "total_mileages": _float_value(
            payload.get("total_mileages"),
            default=sum(record["mileages"] for record in records),
        ),
        "ec": _float_value(
            payload.get("ec"),
            default=sum(record["ec"] for record in records),
        ),
        "used_electricity": _float_value(
            payload.get("used_electricity"),
            default=sum(record["used_electricity"] for record in records),
        ),
        "list": records,
        "detail": detail,
    }


def map_ninebot_battery(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert battery-info to the standalone NinePlus battery contract."""
    battery_list = payload.get("battery_list")
    battery_list = battery_list if isinstance(battery_list, list) else []
    battery = next((item for item in battery_list if isinstance(item, dict)), {})
    battery_main = payload.get("battery_main")
    battery_main = battery_main if isinstance(battery_main, dict) else {}
    charging_protection = payload.get("charging_protection")
    charging_protection = (
        charging_protection if isinstance(charging_protection, dict) else {}
    )

    charging = _int_value(payload.get("charging"), default=0)
    return {
        "electricity": _int_value(
            _first_value("electricity", payload, battery, battery_main),
            default=0,
        ),
        "battery_voltage": _first_float(
            payload.get("battery_voltage"),
            payload.get("bms_volt"),
            battery.get("battery_voltage"),
            battery.get("bms_volt"),
            default=0.0,
        ),
        "battery_temperature": _first_float(
            payload.get("battery_temperature"),
            payload.get("bat_temp"),
            battery.get("battery_temperature"),
            battery.get("bat_temp"),
            default=0.0,
        ),
        "bms_cycle": _int_value(
            _first_value("bms_cycle", payload, battery),
            default=0,
        ),
        "charging_power": _float_value(
            payload.get("charging_power"),
            default=0.0,
        ),
        "charging": charging,
        "remain_charge_time": _float_value(
            payload.get("remain_charge_time"),
            default=0.0,
        ),
        "charge_status": _int_value(
            charging_protection.get("status"),
            default=charging,
        ),
    }


def map_ninebot_travel_detail(
    payload: dict[str, Any],
    travel_id: str,
) -> dict[str, Any]:
    """Convert a real Ninebot ride detail, including its trail, for iOS."""
    record = _map_travel_record(payload, fallback_travel_id=travel_id)
    record["points"] = _travel_points(payload.get("trail"))
    return record


def _nonempty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _vehicle_image_fields(vehicle: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("img_url", "v6_light_img_url", "v6_dark_img_url"):
        value = _nonempty_string(vehicle.get(key))
        if value is not None:
            fields[key] = value
    return fields


def _float_value(value: Any, default: float) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if math.isfinite(converted) else default


def _int_value(value: Any, default: int) -> int:
    return int(_float_value(value, default=float(default)))


def _first_float(*values: Any, default: float) -> float:
    for value in values:
        converted = _float_value(value, default=math.nan)
        if math.isfinite(converted):
            return converted
    return default


def _first_value(key: str, *sources: dict[str, Any]) -> Any:
    for source in sources:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def _map_travel_record(
    record: dict[str, Any],
    fallback_travel_id: str | None = None,
) -> dict[str, Any]:
    travel_id = _nonempty_string(record.get("travel_id")) or fallback_travel_id
    if not travel_id:
        raise NinebotMappingError("Ninebot travel record is missing travel_id")

    duration_seconds = _float_value(record.get("duration"), default=0.0)
    duration_minutes = _float_value(
        record.get("durationMinutes"),
        default=duration_seconds / 60.0,
    )
    return {
        "travel_id": travel_id,
        "start_time": _travel_time(record.get("start_time")),
        "end_time": _travel_time(
            record.get("end_time"),
            formatted=record.get("end_time_format"),
        ),
        "mileages": _float_value(record.get("mileages"), default=0.0),
        "ec": _float_value(record.get("ec"), default=0.0),
        "used_electricity": _float_value(
            record.get("used_electricity"),
            default=0.0,
        ),
        "durationMinutes": duration_minutes,
        "speed": _first_float(
            record.get("speed"),
            record.get("avg_speed"),
            default=0.0,
        ),
    }


def _travel_time(value: Any, formatted: Any = None) -> str:
    timestamp = _float_value(value, default=math.nan)
    if math.isfinite(timestamp):
        if timestamp > 100_000_000_000:
            timestamp /= 1000.0
        try:
            return datetime.fromtimestamp(timestamp, NINEBOT_TIMEZONE).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        except (OverflowError, OSError, ValueError):
            pass
    return _nonempty_string(formatted) or _nonempty_string(value) or ""


def _travel_points(value: Any) -> list[dict[str, float]]:
    if not isinstance(value, str):
        return []
    points: list[dict[str, float]] = []
    normalized = value.replace("|", ";").replace("\r", ";").replace("\n", ";")
    for segment in normalized.split(";"):
        numbers: list[float] = []
        for item in segment.replace("\t", " ").replace(",", " ").split():
            number = _float_value(item, default=math.nan)
            if math.isfinite(number):
                numbers.append(number)
        if len(numbers) < 2:
            continue

        first, second = numbers[0], numbers[1]
        if abs(first) > 90 and abs(second) <= 90:
            longitude, latitude = first, second
        else:
            latitude, longitude = first, second
        if abs(latitude) > 90 or abs(longitude) > 180:
            continue

        point = {
            "lat": latitude,
            "lng": longitude,
            "speed": numbers[2] if len(numbers) >= 3 else 0.0,
        }
        if points and points[-1]["lat"] == latitude and points[-1]["lng"] == longitude:
            continue
        points.append(point)
    return points
