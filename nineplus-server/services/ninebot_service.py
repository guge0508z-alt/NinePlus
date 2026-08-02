"""Read-only access to Ninebot data through the isolated ninecli install."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from services.cache_service import CacheResult, CacheService


SERVER_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_DIR = SERVER_DIR.parent


def _default_ninecli_python() -> Path:
    """Prefer the legacy test venv when present, otherwise use this venv."""
    executable_name = "python.exe" if os.name == "nt" else "python"
    executable_dir = "Scripts" if os.name == "nt" else "bin"
    legacy_python = (
        REPOSITORY_DIR
        / "ninebot-cli-test"
        / ".venv"
        / executable_dir
        / executable_name
    )
    if legacy_python.is_file():
        return legacy_python
    return Path(sys.executable)


def _default_ninecli_config() -> Path:
    """Preserve the local test config, then follow ninecli's cross-platform default."""
    legacy_config = REPOSITORY_DIR / "ninebot-cli-test" / "ninebot-config"
    if legacy_config.is_dir():
        return legacy_config
    return Path.home() / ".config" / "ninebot"


DEFAULT_NINECLI_PYTHON = _default_ninecli_python()
DEFAULT_NINECLI_CONFIG = _default_ninecli_config()
DEFAULT_TIMEOUT_SECONDS = 45.0
VEHICLES_CACHE_TTL_SECONDS = 5 * 60.0
DASHBOARD_CACHE_TTL_SECONDS = 45.0
BATTERY_CACHE_TTL_SECONDS = 5 * 60.0
TRAVEL_CACHE_TTL_SECONDS = 10 * 60.0


class NinebotServiceError(RuntimeError):
    """Raised when ninecli cannot return a usable read-only response."""


class NinebotConfigurationError(NinebotServiceError):
    """Raised when the isolated ninecli installation is unavailable."""


class NinebotAuthenticationError(NinebotServiceError):
    """Raised when the saved Ninebot session is invalid or expired."""


class NinebotService:
    """Run read-only ninecli commands with tokens stored outside this server."""

    def __init__(
        self,
        python_executable: Path | None = None,
        config_dir: Path | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cache_service: CacheService | None = None,
    ) -> None:
        configured_python = os.getenv("NINEBOT_CLI_PYTHON")
        configured_config = os.getenv("NINEBOT_CLI_CONFIG")
        self._python_executable = Path(
            configured_python or python_executable or DEFAULT_NINECLI_PYTHON
        )
        self._config_dir = Path(
            configured_config or config_dir or DEFAULT_NINECLI_CONFIG
        )
        self._timeout_seconds = timeout_seconds
        self._cache = cache_service or CacheService()

    async def fetch_vehicles(self) -> list[dict[str, Any]]:
        """Return the newest cached real vehicle list."""
        return (await self.fetch_vehicles_with_metadata()).value

    async def fetch_vehicles_with_metadata(
        self,
    ) -> CacheResult[list[dict[str, Any]]]:
        """Return vehicles and cache provenance without changing ninecli calls."""
        return await self._cache.get_or_fetch_with_metadata(
            ("vehicles",),
            VEHICLES_CACHE_TTL_SECONDS,
            self._fetch_vehicles_uncached,
            endpoint="vehicles",
        )

    async def _fetch_vehicles_uncached(self) -> list[dict[str, Any]]:
        """Return the decrypted vehicle list produced by ninecli."""
        self._validate_configuration()
        command = (
            str(self._python_executable),
            "-m",
            "ninecli",
            "--config",
            str(self._config_dir),
            "vehicles",
            "--json",
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise NinebotServiceError("ninecli vehicles timed out") from error
        except OSError as error:
            raise NinebotServiceError("ninecli could not be started") from error

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            self._raise_command_error(stderr_text)

        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NinebotServiceError("ninecli returned invalid JSON") from error

        if isinstance(payload, list):
            vehicles = payload
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            vehicles = payload["data"]
        else:
            raise NinebotServiceError("ninecli returned an unexpected vehicle response")

        valid_vehicles = [vehicle for vehicle in vehicles if isinstance(vehicle, dict)]
        if not valid_vehicles:
            raise NinebotServiceError("ninecli returned no usable vehicles")
        return valid_vehicles

    async def get_dashboard(self, sn: str) -> dict[str, Any]:
        """Return the newest cached real vehicle status."""
        return (await self.get_dashboard_with_metadata(sn)).value

    async def get_dashboard_with_metadata(
        self,
        sn: str,
    ) -> CacheResult[dict[str, Any]]:
        """Return vehicle status and cache provenance."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")
        return await self._cache.get_or_fetch_with_metadata(
            ("dashboard", normalized_sn),
            DASHBOARD_CACHE_TTL_SECONDS,
            lambda: self._get_dashboard_uncached(normalized_sn),
            endpoint="dashboard",
            sn=normalized_sn,
        )

    async def _get_dashboard_uncached(self, sn: str) -> dict[str, Any]:
        """Return one vehicle status produced by the read-only ninecli command."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")

        self._validate_configuration()
        vehicles = await self.fetch_vehicles()
        vehicles_by_sn = {
            vehicle["wnumber"].strip(): vehicle
            for vehicle in vehicles
            if isinstance(vehicle.get("wnumber"), str)
            and vehicle["wnumber"].strip()
        }
        if normalized_sn not in vehicles_by_sn:
            raise NinebotServiceError("vehicle SN was not returned by ninecli vehicles")

        command = (
            str(self._python_executable),
            "-m",
            "ninecli",
            "--config",
            str(self._config_dir),
            "status",
            normalized_sn,
            "--json",
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise NinebotServiceError("ninecli status timed out") from error
        except OSError as error:
            raise NinebotServiceError("ninecli could not be started") from error

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            self._raise_command_error(stderr_text, command_name="status")

        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NinebotServiceError("ninecli returned invalid status JSON") from error

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise NinebotServiceError("ninecli returned an unexpected status response")

        returned_sn = payload.get("sn")
        if isinstance(returned_sn, str) and returned_sn.strip() != normalized_sn:
            raise NinebotServiceError("ninecli returned status for another vehicle")
        payload = dict(payload)
        payload["_vehicle"] = dict(vehicles_by_sn[normalized_sn])
        return payload

    async def get_travel(self, sn: str, month: str) -> dict[str, Any]:
        """Return the newest cached real Ninebot travel month."""
        return (await self.get_travel_with_metadata(sn, month)).value

    async def get_travel_with_metadata(
        self,
        sn: str,
        month: str,
    ) -> CacheResult[dict[str, Any]]:
        """Return one travel month and cache provenance."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")
        if len(month) != 6 or not month.isdigit():
            raise NinebotServiceError("travel month must use YYYYMM")
        return await self._cache.get_or_fetch_with_metadata(
            ("travel", normalized_sn, month),
            TRAVEL_CACHE_TTL_SECONDS,
            lambda: self._get_travel_uncached(normalized_sn, month),
            endpoint="travel",
            sn=normalized_sn,
        )

    async def _get_travel_uncached(self, sn: str, month: str) -> dict[str, Any]:
        """Return one real Ninebot travel-list2 month through ninecli."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")
        if len(month) != 6 or not month.isdigit():
            raise NinebotServiceError("travel month must use YYYYMM")

        await self._require_known_vehicle(normalized_sn)
        payload = await self._run_travel_command(
            normalized_sn,
            "--month",
            month,
            command_name="travel list",
        )
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise NinebotServiceError("ninecli returned an unexpected travel response")
        return payload

    async def get_battery(self, sn: str) -> dict[str, Any]:
        """Return the newest cached real battery-info response."""
        return (await self.get_battery_with_metadata(sn)).value

    async def get_battery_with_metadata(
        self,
        sn: str,
    ) -> CacheResult[dict[str, Any]]:
        """Return battery-info and cache provenance."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")
        return await self._cache.get_or_fetch_with_metadata(
            ("battery", normalized_sn),
            BATTERY_CACHE_TTL_SECONDS,
            lambda: self._get_battery_uncached(normalized_sn),
            endpoint="battery",
            sn=normalized_sn,
        )

    async def _get_battery_uncached(self, sn: str) -> dict[str, Any]:
        """Return real /v6/vehicle/battery-info data through ninecli."""
        normalized_sn = sn.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")

        await self._require_known_vehicle(normalized_sn)
        self._validate_configuration()
        command = (
            str(self._python_executable),
            "-m",
            "ninecli",
            "--config",
            str(self._config_dir),
            "battery",
            normalized_sn,
            "--json",
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise NinebotServiceError("ninecli battery timed out") from error
        except OSError as error:
            raise NinebotServiceError("ninecli could not be started") from error

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            self._raise_command_error(stderr_text, command_name="battery")

        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NinebotServiceError("ninecli returned invalid battery JSON") from error

        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise NinebotServiceError("ninecli returned an unexpected battery response")
        return payload

    async def get_travel_detail(self, sn: str, travel_id: str) -> dict[str, Any]:
        """Return the newest cached real Ninebot ride detail."""
        return (await self.get_travel_detail_with_metadata(sn, travel_id)).value

    async def get_travel_detail_with_metadata(
        self,
        sn: str,
        travel_id: str,
    ) -> CacheResult[dict[str, Any]]:
        """Return one ride detail and cache provenance."""
        normalized_sn = sn.strip()
        normalized_travel_id = travel_id.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")
        if not normalized_travel_id:
            raise NinebotServiceError("travel ID is empty")
        return await self._cache.get_or_fetch_with_metadata(
            ("travel_detail", normalized_sn, normalized_travel_id),
            TRAVEL_CACHE_TTL_SECONDS,
            lambda: self._get_travel_detail_uncached(
                normalized_sn,
                normalized_travel_id,
            ),
            endpoint="travel_detail",
            sn=normalized_sn,
        )

    async def _get_travel_detail_uncached(
        self,
        sn: str,
        travel_id: str,
    ) -> dict[str, Any]:
        """Return one real read-only Ninebot ride detail through ninecli."""
        normalized_sn = sn.strip()
        normalized_travel_id = travel_id.strip()
        if not normalized_sn:
            raise NinebotServiceError("vehicle SN is empty")
        if not normalized_travel_id:
            raise NinebotServiceError("travel ID is empty")

        await self._require_known_vehicle(normalized_sn)
        payload = await self._run_travel_command(
            normalized_sn,
            "--detail",
            normalized_travel_id,
            command_name="travel detail",
        )
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            payload = payload["data"]
        if not isinstance(payload, dict):
            raise NinebotServiceError(
                "ninecli returned an unexpected travel detail response"
            )
        return payload

    async def _require_known_vehicle(self, sn: str) -> None:
        vehicles = await self.fetch_vehicles()
        known_sns = {
            vehicle["wnumber"].strip()
            for vehicle in vehicles
            if isinstance(vehicle.get("wnumber"), str)
            and vehicle["wnumber"].strip()
        }
        if sn not in known_sns:
            raise NinebotServiceError("vehicle SN was not returned by ninecli vehicles")

    async def _run_travel_command(
        self,
        sn: str,
        flag: str,
        value: str,
        command_name: str,
    ) -> Any:
        self._validate_configuration()
        command = (
            str(self._python_executable),
            "-m",
            "ninecli",
            "--config",
            str(self._config_dir),
            "travel",
            sn,
            flag,
            value,
            "--json",
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as error:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise NinebotServiceError(f"ninecli {command_name} timed out") from error
        except OSError as error:
            raise NinebotServiceError("ninecli could not be started") from error

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            self._raise_command_error(stderr_text, command_name=command_name)

        try:
            return json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise NinebotServiceError(
                f"ninecli returned invalid {command_name} JSON"
            ) from error

    def _validate_configuration(self) -> None:
        if not self._python_executable.is_file():
            raise NinebotConfigurationError("ninecli Python executable is missing")
        if not self._config_dir.is_dir():
            raise NinebotConfigurationError("ninecli config directory is missing")
        if not (self._config_dir / "tokens.json").is_file():
            raise NinebotAuthenticationError("ninecli tokens are missing")

    @staticmethod
    def _raise_command_error(stderr: str, command_name: str = "vehicles") -> None:
        normalized = stderr.lower()
        authentication_markers = (
            "login first",
            "invalid username or password",
            "refresh access_token",
            "refresh_token invalid",
            "business_login auto-fallback failed",
            "invalid_auth",
            "token_expired",
            "load tokens",
        )
        if any(marker in normalized for marker in authentication_markers):
            raise NinebotAuthenticationError("saved Ninebot authentication is invalid")
        raise NinebotServiceError(f"ninecli {command_name} failed")
