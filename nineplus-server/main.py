"""NinePlus 局域网模拟服务器。"""

from datetime import datetime, timezone
import hmac
import logging
import os
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from adapters.ninebot_mapper import (
    NinebotMappingError,
    map_ninebot_battery,
    map_ninebot_dashboard,
    map_ninebot_travel,
    map_ninebot_travel_detail,
    map_ninebot_vehicles,
)
from services.cache_service import CacheMetadata
from services.ninebot_service import NinebotService, NinebotServiceError


def _environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


TEST_MODE = _environment_flag("TEST_MODE", default=False)
NINEPLUS_API_KEY = os.getenv("NINEPLUS_API_KEY", "").strip()
TEST_SESSION_TOKEN = "test-session-token"
LOGGER = logging.getLogger(__name__)
NINEBOT_SERVICE = NinebotService()


class HealthData(BaseModel):
    status: str
    service: str


class LoginRequest(BaseModel):
    account: str
    password: str


class LoginData(BaseModel):
    uuid: str
    phone: str
    area_code: str
    region: str
    business_uid: str
    account_id: int
    session_token: str


class VehicleData(BaseModel):
    wnumber: str
    device_name: str
    vehicle_name_en: str
    img_url: str | None = None
    v6_light_img_url: str | None = None
    v6_dark_img_url: str | None = None


class HealthResponse(BaseModel):
    ok: Literal[True]
    data: HealthData


class LoginResponse(BaseModel):
    ok: Literal[True]
    data: LoginData


class VehiclesData(BaseModel):
    vehicles: list[VehicleData]
    source: Literal["ninebot", "cache"] | None = None
    updated_at: datetime | None = None
    stale: bool | None = None


class VehiclesResponse(BaseModel):
    ok: Literal[True]
    data: VehiclesData


class DashboardStateData(BaseModel):
    dump_energy: int
    estimate_mileage: float
    charging: int
    pwr: int
    lock_status: int


class DashboardLocationData(BaseModel):
    lat: float
    lon: float
    lock: int


class DashboardStateWithLocationData(DashboardStateData):
    loc: DashboardLocationData | None = None


class DashboardBatteryData(BaseModel):
    electricity: int
    battery_voltage: float
    battery_temperature: float
    bms_cycle: int
    charging_power: float
    charging: int
    remain_charge_time: float


class BatteryDetailData(DashboardBatteryData):
    charge_status: int
    source: Literal["ninebot", "cache"] | None = None
    updated_at: datetime | None = None
    stale: bool | None = None


class DashboardTravelUnavailableData(BaseModel):
    available: Literal[False]
    stale: Literal[False] = False
    error: str


class DashboardData(BaseModel):
    vehicle: VehicleData
    state: DashboardStateWithLocationData
    battery: BatteryDetailData
    travel: "DashboardTravelData | DashboardTravelUnavailableData"
    updated_at: datetime
    source: Literal["ninebot", "cache"] | None = None
    stale: bool | None = None


class DashboardResponse(BaseModel):
    ok: Literal[True]
    data: DashboardData


class StatusResponse(BaseModel):
    ok: Literal[True]
    data: DashboardStateData


class BatteryResponse(BaseModel):
    ok: Literal[True]
    data: BatteryDetailData


class ControlData(BaseModel):
    sn: str
    action: str
    status: Literal["success"]


class ControlResponse(BaseModel):
    ok: Literal[True]
    data: ControlData


class TravelRideData(BaseModel):
    travel_id: str
    start_time: str
    end_time: str
    mileages: float
    ec: float
    used_electricity: float
    durationMinutes: float
    speed: float


class TravelTrackPointData(BaseModel):
    lat: float
    lng: float
    speed: float


class TravelDetailData(TravelRideData):
    points: list[TravelTrackPointData]
    source: Literal["ninebot", "cache"] | None = None
    updated_at: datetime | None = None
    stale: bool | None = None


class TravelDetailResponse(BaseModel):
    ok: Literal[True]
    data: TravelDetailData


class TravelData(BaseModel):
    month: str
    total_mileages: float
    ec: float
    used_electricity: float
    list: list[TravelRideData]
    detail: list[float]
    source: Literal["ninebot", "cache"] | None = None
    updated_at: datetime | None = None
    stale: bool | None = None


class DashboardTravelData(TravelData):
    available: Literal[True] = True
    stale: bool = False
    error: str = ""


class TravelResponse(BaseModel):
    ok: Literal[True]
    data: TravelData


class TravelSyncData(BaseModel):
    month: str
    page: int
    page_size: int
    total: int
    has_more: bool
    list: list[TravelRideData]
    source: Literal["ninebot", "cache"] | None = None
    updated_at: datetime | None = None
    stale: bool | None = None


class TravelSyncResponse(BaseModel):
    ok: Literal[True]
    data: TravelSyncData


DashboardData.model_rebuild()


def _cache_metadata_fields(metadata: CacheMetadata) -> dict[str, object]:
    return {
        "source": metadata.source,
        "updated_at": metadata.updated_at,
        "stale": metadata.stale,
    }


app = FastAPI(
    title="NinePlus Test Server",
    description="用于验证 NinePlus iOS App 能否连接 Windows 电脑。",
    version="0.1.0",
)


@app.middleware("http")
async def require_nineplus_api_key(request: Request, call_next):
    """Protect every current and future endpoint except the health probe."""
    if request.url.path == "/healthz":
        return await call_next(request)

    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied_key = authorization.partition(" ")
    is_authorized = (
        bool(NINEPLUS_API_KEY)
        and bool(separator)
        and scheme.lower() == "bearer"
        and bool(supplied_key)
        and hmac.compare_digest(supplied_key, NINEPLUS_API_KEY)
    )
    if not is_authorized:
        return _unauthorized()

    return await call_next(request)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """返回 NinePlus 客户端支持的成功响应信封。"""
    return HealthResponse(
        ok=True,
        data=HealthData(status="ok", service="nineplus-test-server"),
    )


@app.post("/accounts/login", response_model=LoginResponse)
async def login(credentials: LoginRequest) -> LoginResponse | JSONResponse:
    """为任意非空测试凭据创建无状态客户端会话。"""
    account = credentials.account.strip()
    if not account or not credentials.password.strip():
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {
                    "code": "missing_credentials",
                    "message": "账号和密码不能为空",
                },
            },
        )

    return LoginResponse(
        ok=True,
        data=LoginData(
            uuid="00000000-0000-0000-0000-000000000001",
            phone=account,
            area_code="86",
            region="CN",
            business_uid="TEST-BUSINESS-UID",
            account_id=1,
            session_token=TEST_SESSION_TOKEN,
        ),
    )


@app.get(
    "/vehicles",
    response_model=VehiclesResponse,
    response_model_exclude_none=True,
)
async def vehicles() -> VehiclesResponse | JSONResponse:
    """Return real vehicles, allowing mock fallback only in test mode."""
    vehicles_metadata: CacheMetadata | None = None
    try:
        vehicles_result = await NINEBOT_SERVICE.fetch_vehicles_with_metadata()
        vehicles_metadata = vehicles_result.metadata
        mapped_vehicles = map_ninebot_vehicles(vehicles_result.value)
        vehicle_models = [VehicleData(**vehicle) for vehicle in mapped_vehicles]
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            return _service_unavailable("vehicles", error)
        LOGGER.warning("vehicles failed (%s); using test data", type(error).__name__)
        vehicle_models = _simulated_vehicles()

    return VehiclesResponse(
        ok=True,
        data=VehiclesData(
            vehicles=vehicle_models,
            **(
                _cache_metadata_fields(vehicles_metadata)
                if vehicles_metadata is not None
                else {}
            ),
        ),
    )


def _simulated_vehicles() -> list[VehicleData]:
    """Return the original safe fallback used before the real integration."""
    return [
        VehicleData(
            wnumber="TEST000001",
            device_name="我的 NX Mix",
            vehicle_name_en="NX Mix",
        )
    ]


@app.get(
    "/vehicles/{sn}/dashboard",
    response_model=DashboardResponse,
    response_model_exclude_none=True,
)
async def vehicle_dashboard(sn: str) -> DashboardResponse | JSONResponse:
    """Return a real dashboard, allowing mock data only in test mode."""
    if sn == "TEST000001":
        if not TEST_MODE:
            return _vehicle_not_found()
        return _simulated_dashboard()

    try:
        status_result = await NINEBOT_SERVICE.get_dashboard_with_metadata(sn)
        mapped = map_ninebot_dashboard(status_result.value, sn)
        dashboard_battery = await _dashboard_battery(sn)
        current_month = datetime.now().astimezone().strftime("%Y%m")
        dashboard_travel = await _dashboard_travel(sn, current_month)
        return DashboardResponse(
            ok=True,
            data=DashboardData(
                vehicle=VehicleData(**mapped["vehicle"]),
                state=DashboardStateWithLocationData(**mapped["state"]),
                battery=dashboard_battery,
                travel=dashboard_travel,
                updated_at=status_result.metadata.updated_at,
                source=status_result.metadata.source,
                stale=status_result.metadata.stale,
            ),
        )
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            return _service_unavailable("dashboard", error, sn)
        LOGGER.warning("dashboard failed (%s); using test data", type(error).__name__)
        return _simulated_dashboard()


async def _dashboard_battery(
    sn: str,
) -> BatteryDetailData:
    """Return the required real battery payload for the dashboard."""
    try:
        battery_result = await NINEBOT_SERVICE.get_battery_with_metadata(sn)
        mapped_battery = map_ninebot_battery(battery_result.value)
        return BatteryDetailData(
            **mapped_battery,
            **_cache_metadata_fields(battery_result.metadata),
        )
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            raise
        LOGGER.warning("dashboard battery failed (%s); using test data", type(error).__name__)
        return _simulated_battery().data


async def _dashboard_travel(
    sn: str,
    month: str,
) -> DashboardTravelData | DashboardTravelUnavailableData:
    """Keep the dashboard usable when the optional travel request fails."""
    try:
        travel_result = await NINEBOT_SERVICE.get_travel_with_metadata(sn, month)
        mapped_travel = map_ninebot_travel(travel_result.value, month)
        return DashboardTravelData(
            **mapped_travel,
            **_cache_metadata_fields(travel_result.metadata),
            error=(
                "ninebot_travel_unavailable"
                if travel_result.metadata.stale
                else ""
            ),
        )
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            LOGGER.warning(
                "dashboard travel failed (%s); marking travel unavailable",
                type(error).__name__,
            )
            return DashboardTravelUnavailableData(
                available=False,
                stale=False,
                error="ninebot_travel_unavailable",
            )
        LOGGER.warning("dashboard travel failed (%s); using test data", type(error).__name__)
        return _dashboard_test_travel("202607")


def _simulated_dashboard() -> DashboardResponse:
    """Return the original TEST000001 dashboard fallback."""
    return DashboardResponse(
        ok=True,
        data=DashboardData(
            vehicle=VehicleData(
                wnumber="TEST000001",
                device_name="我的 NX Mix",
                vehicle_name_en="NX Mix",
            ),
            state=DashboardStateWithLocationData(
                dump_energy=77,
                estimate_mileage=55.0,
                charging=0,
                pwr=0,
                lock_status=0,
            ),
            battery=BatteryDetailData(
                electricity=77,
                battery_voltage=48.5,
                battery_temperature=26.0,
                bms_cycle=10,
                charging_power=0.0,
                charging=0,
                remain_charge_time=0.0,
                charge_status=0,
            ),
            travel=_dashboard_test_travel("202607"),
            updated_at=datetime.now(timezone.utc),
        ),
    )


@app.get("/vehicles/{sn}/status", response_model=StatusResponse)
async def vehicle_status(sn: str) -> StatusResponse | JSONResponse:
    """返回模拟车辆状态。"""
    if sn == "TEST000001":
        if not TEST_MODE:
            return _vehicle_not_found()
    elif not TEST_MODE:
        return _service_unavailable("status", sn=sn)
    else:
        return _vehicle_not_found()

    return StatusResponse(
        ok=True,
        data=DashboardStateData(
            dump_energy=77,
            estimate_mileage=55.0,
            charging=0,
            pwr=0,
            lock_status=0,
        ),
    )


@app.get(
    "/vehicles/{sn}/battery",
    response_model=BatteryResponse,
    response_model_exclude_none=True,
)
async def vehicle_battery(sn: str) -> BatteryResponse | JSONResponse:
    """Return real battery-info, allowing mock data only in test mode."""
    if sn == "TEST000001":
        if not TEST_MODE:
            return _vehicle_not_found()
        return _simulated_battery()

    try:
        battery_result = await NINEBOT_SERVICE.get_battery_with_metadata(sn)
        mapped_battery = map_ninebot_battery(battery_result.value)
        return BatteryResponse(
            ok=True,
            data=BatteryDetailData(
                **mapped_battery,
                **_cache_metadata_fields(battery_result.metadata),
            ),
        )
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            return _service_unavailable("battery", error, sn)
        LOGGER.warning("battery failed (%s); using test data", type(error).__name__)
        return _simulated_battery()


def _simulated_battery() -> BatteryResponse:
    """Return the original standalone simulated battery values."""
    return BatteryResponse(
        ok=True,
        data=BatteryDetailData(
            electricity=77,
            battery_voltage=48.5,
            battery_temperature=26.0,
            bms_cycle=10,
            charging_power=0.0,
            charging=0,
            remain_charge_time=0.0,
            charge_status=0,
        ),
    )


@app.post("/vehicles/{sn}/bell", response_model=ControlResponse)
async def ring_bell(sn: str) -> ControlResponse | JSONResponse:
    """模拟鸣笛成功。"""
    return _control_success(sn, "bell")


@app.post("/vehicles/{sn}/buck", response_model=ControlResponse)
async def open_bucket(sn: str) -> ControlResponse | JSONResponse:
    """模拟开座桶成功。"""
    return _control_success(sn, "buck")


@app.post("/vehicles/{sn}/engine/start", response_model=ControlResponse)
async def engine_start(sn: str) -> ControlResponse | JSONResponse:
    """模拟启动电机成功。"""
    return _control_success(sn, "engine_start")


@app.post("/vehicles/{sn}/engine/stop", response_model=ControlResponse)
async def engine_stop(sn: str) -> ControlResponse | JSONResponse:
    """模拟关闭电机成功。"""
    return _control_success(sn, "engine_stop")


@app.get(
    "/vehicles/{sn}/travel",
    response_model=TravelResponse,
    response_model_exclude_none=True,
)
async def vehicle_travel(sn: str, month: str) -> TravelResponse | JSONResponse:
    """Return a real read-only travel month with the existing mock fallback."""
    if len(month) != 6 or not month.isdigit():
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {
                    "code": "invalid_month",
                    "message": "month 必须使用 yyyyMM 格式",
                },
            },
        )
    if sn == "TEST000001":
        if not TEST_MODE:
            return _vehicle_not_found()
        return TravelResponse(ok=True, data=_travel_data(month))

    try:
        travel_result = await NINEBOT_SERVICE.get_travel_with_metadata(sn, month)
        mapped = map_ninebot_travel(travel_result.value, month)
        return TravelResponse(
            ok=True,
            data=TravelData(
                **mapped,
                **_cache_metadata_fields(travel_result.metadata),
            ),
        )
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            return _service_unavailable("travel", error, sn)
        LOGGER.warning("travel failed (%s); using test data", type(error).__name__)
        return TravelResponse(ok=True, data=_travel_data(month))


@app.post(
    "/vehicles/{sn}/travel-sync",
    response_model=TravelSyncResponse,
    response_model_exclude_none=True,
)
async def sync_vehicle_travel(
    sn: str,
    month: str,
    page_size: int = 20,
) -> TravelSyncResponse | JSONResponse:
    """Synchronize one real travel month with a safe compatible fallback."""
    if len(month) != 6 or not month.isdigit():
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {
                    "code": "invalid_month",
                    "message": "month 必须使用 yyyyMM 格式",
                },
            },
        )
    if page_size <= 0:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": {
                    "code": "invalid_page_size",
                    "message": "page_size 必须大于 0",
                },
            },
        )

    if sn == "TEST000001":
        if not TEST_MODE:
            return _vehicle_not_found()
        travel = _travel_data(month)
    else:
        try:
            travel_result = await NINEBOT_SERVICE.get_travel_with_metadata(sn, month)
            mapped_travel = map_ninebot_travel(travel_result.value, month)
            travel = TravelData(
                **mapped_travel,
                **_cache_metadata_fields(travel_result.metadata),
            )
        except (NinebotServiceError, NinebotMappingError, ValueError) as error:
            if not TEST_MODE:
                return _service_unavailable("travel-sync", error, sn)
            LOGGER.warning(
                "travel-sync failed (%s); using empty test response",
                type(error).__name__,
            )
            travel = TravelData(
                month=month,
                total_mileages=0.0,
                ec=0.0,
                used_electricity=0.0,
                list=[],
                detail=[],
            )

    records = travel.list[:page_size]
    return TravelSyncResponse(
        ok=True,
        data=TravelSyncData(
            month=month,
            page=1,
            page_size=page_size,
            total=len(travel.list),
            has_more=len(records) < len(travel.list),
            list=records,
            source=travel.source,
            updated_at=travel.updated_at,
            stale=travel.stale,
        ),
    )


@app.get(
    "/vehicles/{sn}/travel/{travel_id}",
    response_model=TravelDetailResponse,
    response_model_exclude_none=True,
)
async def vehicle_travel_detail(
    sn: str,
    travel_id: str,
) -> TravelDetailResponse | JSONResponse:
    """Return one real ride detail and its track, preserving mock fallback."""
    if sn == "TEST000001":
        if not TEST_MODE:
            return _vehicle_not_found()
        return _simulated_travel_detail(travel_id)

    try:
        detail_result = await NINEBOT_SERVICE.get_travel_detail_with_metadata(
            sn,
            travel_id,
        )
        mapped = map_ninebot_travel_detail(detail_result.value, travel_id)
        return TravelDetailResponse(
            ok=True,
            data=TravelDetailData(
                **mapped,
                **_cache_metadata_fields(detail_result.metadata),
            ),
        )
    except (NinebotServiceError, NinebotMappingError, ValueError) as error:
        if not TEST_MODE:
            return _service_unavailable("travel-detail", error, sn)
        LOGGER.warning("travel detail failed (%s); using test data", type(error).__name__)
        return _simulated_travel_detail(travel_id)


def _simulated_travel_detail(
    travel_id: str,
) -> TravelDetailResponse | JSONResponse:
    """Return the original 202607 mock detail when its ID is requested."""

    record = next(
        (
            item
            for item in _travel_data("202607").list
            if item.travel_id == travel_id
        ),
        None,
    )
    if record is None:
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": {
                    "code": "travel_not_found",
                    "message": "未找到模拟行程",
                },
            },
        )

    return TravelDetailResponse(
        ok=True,
        data=TravelDetailData(
            **record.model_dump(),
            points=_travel_track_points(),
        ),
    )


def _dashboard_test_travel(month: str) -> DashboardTravelData:
    """Expose existing simulated travel only when TEST_MODE permits it."""
    return DashboardTravelData(**_travel_data(month).model_dump(exclude_none=True))


def _travel_data(month: str) -> TravelData:
    if month != "202607":
        return TravelData(
            month=month,
            total_mileages=0.0,
            ec=0.0,
            used_electricity=0.0,
            list=[],
            detail=[],
        )

    daily_mileages = [0.0] * 31
    for day, mileage in [(3, 6.4), (7, 9.8), (12, 12.5), (18, 7.3), (24, 15.0)]:
        daily_mileages[day - 1] = mileage

    return TravelData(
        month=month,
        total_mileages=51.0,
        ec=3.38,
        used_electricity=42.0,
        list=[
            TravelRideData(
                travel_id="TEST-RIDE-20260703",
                start_time="2026-07-03 08:10:00",
                end_time="2026-07-03 08:32:00",
                mileages=6.4,
                ec=0.42,
                used_electricity=5.0,
                durationMinutes=22.0,
                speed=17.5,
            ),
            TravelRideData(
                travel_id="TEST-RIDE-20260707",
                start_time="2026-07-07 18:20:00",
                end_time="2026-07-07 18:52:00",
                mileages=9.8,
                ec=0.65,
                used_electricity=8.0,
                durationMinutes=32.0,
                speed=18.4,
            ),
            TravelRideData(
                travel_id="TEST-RIDE-20260712",
                start_time="2026-07-12 07:45:00",
                end_time="2026-07-12 08:23:00",
                mileages=12.5,
                ec=0.82,
                used_electricity=10.0,
                durationMinutes=38.0,
                speed=19.7,
            ),
            TravelRideData(
                travel_id="TEST-RIDE-20260718",
                start_time="2026-07-18 20:05:00",
                end_time="2026-07-18 20:31:00",
                mileages=7.3,
                ec=0.50,
                used_electricity=6.0,
                durationMinutes=26.0,
                speed=16.8,
            ),
            TravelRideData(
                travel_id="TEST-RIDE-20260724",
                start_time="2026-07-24 09:15:00",
                end_time="2026-07-24 10:00:00",
                mileages=15.0,
                ec=0.99,
                used_electricity=13.0,
                durationMinutes=45.0,
                speed=20.0,
            ),
        ],
        detail=daily_mileages,
    )


def _travel_track_points() -> list[TravelTrackPointData]:
    """返回客户端可识别的模拟地图轨迹点。"""
    return [
        TravelTrackPointData(lat=31.23040, lng=121.47370, speed=0.0),
        TravelTrackPointData(lat=31.23105, lng=121.47510, speed=12.5),
        TravelTrackPointData(lat=31.23210, lng=121.47720, speed=20.0),
        TravelTrackPointData(lat=31.23325, lng=121.47900, speed=18.2),
        TravelTrackPointData(lat=31.23410, lng=121.48060, speed=0.0),
    ]


def _control_success(sn: str, action: str) -> ControlResponse | JSONResponse:
    if not TEST_MODE or sn != "TEST000001":
        return _vehicle_not_found()
    return ControlResponse(
        ok=True,
        data=ControlData(sn=sn, action=action, status="success"),
    )


def _vehicle_not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "ok": False,
            "error": {
                "code": "vehicle_not_found",
                "message": "未找到车辆",
            },
        },
    )


def _unauthorized() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
        content={
            "ok": False,
            "error": {
                "code": "nineplus_unauthorized",
                "message": "NinePlus API Key 缺失或无效",
            },
        },
    )


def _service_unavailable(
    endpoint: str,
    error: Exception | None = None,
    sn: str | None = None,
) -> JSONResponse:
    """Return a stable real-mode error without exposing credentials or internals."""
    error_name = type(error).__name__ if error is not None else "Unavailable"
    LOGGER.warning(
        "%s request failed (%s)%s",
        endpoint,
        error_name,
        f" for SN {sn}" if sn else "",
    )
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "error": {
                "code": "ninebot_service_unavailable",
                "message": "九号真实数据暂时不可用，请稍后重试",
            },
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=19009)
