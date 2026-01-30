"""
Adds support for the Salus Thermostat units.
"""
import asyncio
from dataclasses import dataclass
import json
import logging
import re
import time

import aiohttp
import async_timeout
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_ID,
    UnitOfTemperature,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.dispatcher import async_dispatcher_send

from . import (
    __version__,
    DOMAIN,
    DEFAULT_NAME,
    CONF_NAME,
    URL_LOGIN,
    URL_GET_DEVICES,
    URL_GET_TOKEN,
    URL_GET_DATA,
    URL_SET_DATA,
    URL_SET_SCHEDULE,
    MIN_TEMP,
    MAX_TEMP,
    SCAN_INTERVAL,
    NEWDEVICE_SCAN_INTERVAL,
    SCHEDULE_DAYS,
    SCHEDULE_SERVICE,
    SIGNAL_NEW_DEVICE,
)

try:
    from homeassistant.components.climate import ClimateEntity
except ImportError:
    from homeassistant.components.climate import ClimateDevice as ClimateEntity

from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_PASSWORD,
    CONF_USERNAME,
    UnitOfTemperature,
)

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = ClimateEntityFeature.TARGET_TEMPERATURE
SUPPORT_PRESET = []

REQUEST_TIMEOUT = 20


def _parse_temp(value: str) -> float:
    return float(re.sub(r"[^\d.]", "", value))


def _build_schedule_from_payload(payload: dict) -> dict:
    schedule = {}
    for day in SCHEDULE_DAYS:
        key = f"z1p{day}"
        day_data = payload.get(key, {})
        entries = []
        for i in range(1, 7):
            time_key = f"z1p{day}{i}Time"
            temp_key = f"z1p{day}{i}Temp"
            if time_key in day_data and temp_key in day_data:
                entries.append({
                    "time": day_data[time_key],
                    "temp": _parse_temp(day_data[temp_key]),
                })
        schedule[day] = entries
    return schedule


async def async_first_login(coordinator) -> list:
    """Login to Salus IT500 and retrieve device list."""
    payload = {
        "IDemail": coordinator._username,
        "password": coordinator._password,
        "login": "Login",
        "keep_logged_in": "1",
    }
    headers = {"content-type": "application/x-www-form-urlencoded"}

    try:
        async with async_timeout.timeout(REQUEST_TIMEOUT):
            async with coordinator._session.post(URL_LOGIN, data=payload, headers=headers) as resp:
                resp.raise_for_status()

        async with async_timeout.timeout(REQUEST_TIMEOUT):
            async with coordinator._session.get(URL_GET_DEVICES) as resp:
                resp.raise_for_status()
                text = await resp.text()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise UpdateFailed(f"Error logging in or getting devices: {err}") from err

    try:
        devices: list[SalusFirstLoginResponseItem] = []

        first_div_re = re.compile(r'<div[^>]*class="deviceList\s+(\d+)"[^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE)
        next_div_re = re.compile(r'<div[^>]*class="deviceList"[^>]*>(.*?)</div>', re.DOTALL | re.IGNORECASE)

        for m in first_div_re.finditer(text):
            first_html = m.group(2)

            id_match = re.search(
                r'<input[^>]+name=["\']devId["\'][^>]+value=["\']([^"\']+)["\']',
                first_html,
                re.IGNORECASE,
            )
            dev_id = id_match.group(1).strip() if id_match else m.group(1).strip()

            name_match = re.search(
                r'<a[^>]*class=["\'][^"\']*deviceIcon[^"\']*["\'][^>]*>(.*?)</a>',
                first_html,
                re.DOTALL | re.IGNORECASE,
            )
            dev_name = name_match.group(1).strip() if name_match else ""

            token = None
            next_m = next_div_re.search(text, pos=m.end())
            if next_m:
                next_html = next_m.group(1)
                token_match = re.search(
                    r'<input[^>]+id=["\']token["\'][^>]+value=["\']([^"\']+)["\']',
                    next_html,
                    re.IGNORECASE,
                )
                if token_match:
                    token = token_match.group(1).strip()

            item = SalusFirstLoginResponseItem()
            item.devId = dev_id
            item.devName = re.sub(r"\s+", " ", dev_name)
            item.devToken = token
            devices.append(item)

        if not devices:
            raise UpdateFailed("No devices found in login response")

        _LOGGER.info("Salus IT500 login and get_devices OK")
        return devices
    except (ValueError, KeyError) as err:
        raise UpdateFailed(f"Invalid devices response: {err}") from err


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Salus Thermostat from config entry."""
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    temp_coordinator = SalusDataUpdateCoordinator(hass, username, password, "")
    devices = await async_first_login(temp_coordinator)

    hass.data.setdefault(DOMAIN, {})

    entities = []
    for item in devices:
        device_id = item.devId
        coord = hass.data[DOMAIN].get(device_id)
        if coord is None:
            coord = SalusDataUpdateCoordinator(hass, username, password, device_id)
            await coord.async_refresh()
            hass.data[DOMAIN][device_id] = coord
            async_dispatcher_send(hass, SIGNAL_NEW_DEVICE, device_id)

        device_name = item.devName or f"{name} {device_id}"
        entities.append(SalusThermostat(device_name, device_id, coord))

    async_add_entities(entities)

    async def _handle_set_schedule(call):
        device = call.data.get("device_id")
        if not device:
            raise ValueError("device_id is required")
        coordinator = hass.data[DOMAIN].get(device)
        if coordinator is None:
            raise UpdateFailed("Unknown device")
        day = call.data["day"]
        entries = call.data["entries"]
        await coordinator.async_set_schedule(day, entries)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SCHEDULE_SERVICE,
        _handle_set_schedule,
        schema=vol.Schema({
            vol.Required("device_id"): cv.string,
            vol.Required("day"): cv.string,
            vol.Required("entries"): list,
        }),
    )

    async def _rescan_devices(now):
        try:
            found = await async_first_login(temp_coordinator)
        except Exception as err:
            _LOGGER.debug("Salus IT500 Device rescan failed: %s", err)
            return

        new_entities = []
        for item in found:
            dev_id = item.devId
            if dev_id in hass.data[DOMAIN]:
                continue
            coord = SalusDataUpdateCoordinator(hass, username, password, dev_id)
            try:
                await coord.async_refresh()
            except Exception as err:
                _LOGGER.debug("Salus IT500 Failed to init coordinator for %s: %s", dev_id, err)
                continue
            hass.data[DOMAIN][dev_id] = coord
            async_dispatcher_send(hass, SIGNAL_NEW_DEVICE, dev_id)
            device_name = item.devName or f"{name} {dev_id}"
            new_entities.append(SalusThermostat(device_name, dev_id, coord))

        if new_entities:
            async_add_entities(new_entities)
            _LOGGER.info("Added %d new Salus IT500 climate device(s)", len(new_entities))

    async_track_time_interval(hass, _rescan_devices, NEWDEVICE_SCAN_INTERVAL)


class SalusDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator to manage Salus IT500 data polling."""

    def __init__(self, hass, username: str, password: str, device_id: str):
        self._username = username
        self._password = password
        self._device_id = device_id
        self._token = None
        self._session = async_get_clientsession(hass)

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict:
        return await self._async_get_data()

    async def _async_get_token(self) -> str:
        payload = {
            "IDemail": self._username,
            "password": self._password,
            "login": "Login",
            "keep_logged_in": "1",
        }
        headers = {"content-type": "application/x-www-form-urlencoded"}

        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.post(URL_LOGIN, data=payload, headers=headers) as resp:
                    resp.raise_for_status()

            params = {"devId": self._device_id}
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.get(URL_GET_TOKEN, params=params) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error getting token: {err}") from err

        # robustly parse token input regardless of attribute order
        result = re.search(
            r'<input[^>]*\bid=["\']token["\'][^>]*\bvalue=["\']([^"\']+)["\']',
            text,
            re.IGNORECASE,
        )
        if not result:
            raise UpdateFailed("Token not found in response")

        self._token = result.group(1)
        _LOGGER.info("Salus IT500 get_token OK")
        return self._token

    async def _async_get_data(self) -> dict:
        if self._token is None:
            await self._async_get_token()

        params = {
            "devId": self._device_id,
            "token": self._token,
            "&_": str(int(round(time.time() * 1000))),
        }

        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.get(URL_GET_DATA, params=params) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error getting data: {err}") from err

        try:
            data = json.loads(text)
            _LOGGER.info("Salus IT500 get_data output OK")

            status = data.get("CH1heatOnOffStatus")
            mode = data.get("CH1heatOnOff")

            return {
                "target_temperature": float(data["CH1currentSetPoint"]),
                "current_temperature": float(data["CH1currentRoomTemp"]),
                "frost": float(data["frost"]),
                "status": "ON" if status == "1" else "OFF",
                "operation_mode": "OFF" if mode == "1" else "ON",
                "schedule": _build_schedule_from_payload(data),
            }
        except (ValueError, KeyError) as err:
            raise UpdateFailed(f"Invalid data response: {err}") from err

    async def _async_post(self, payload: dict, url: str = URL_SET_DATA) -> None:
        headers = {"content-type": "application/x-www-form-urlencoded"}
        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.post(url, data=payload, headers=headers) as resp:
                    resp.raise_for_status()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise UpdateFailed(f"Error posting data: {err}") from err

    async def async_set_temperature(self, temperature: float) -> None:
        if self._token is None:
            await self._async_get_token()

        payload = {
            "token": self._token,
            "devId": self._device_id,
            "tempUnit": "0",
            "current_tempZ1_set": "1",
            "current_tempZ1": temperature,
        }

        await self._async_post(payload)
        _LOGGER.info("Salus IT500 set_temperature OK")

    async def async_set_frost(self, frost: float) -> None:
        if self._token is None:
            await self._async_get_token()

        payload = {
            "token": self._token,
            "devId": self._device_id,
            "tempUnit": "0",
            "frost_temp_set": "1",
            "frost_temp": frost,
        }

        await self._async_post(payload)
        _LOGGER.info("Salus IT500 set_frost OK")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if self._token is None:
            await self._async_get_token()

        if hvac_mode == HVACMode.OFF:
            payload = {"token": self._token, "devId": self._device_id, "auto": "1", "auto_setZ1": "1"}
        else:
            payload = {"token": self._token, "devId": self._device_id, "auto": "0", "auto_setZ1": "1"}

        await self._async_post(payload)
        _LOGGER.info("Salus IT500 set_hvac_mode OK")

    async def async_set_schedule(self, day: str, entries: list[dict]) -> None:
        day = day.capitalize()
        if day not in SCHEDULE_DAYS:
            raise ValueError("Invalid day")

        if self._token is None:
            await self._async_get_token()

        entries = SalusThermostat._normalize_schedule_entries(entries)
        day_key = day.lower()

        payload = {
            "token": self._token,
            "devId": self._device_id,
            "tempUnit": "0",
            f"z1p{day_key}Set": "1",
        }

        for i, entry in enumerate(entries[:6], start=1):
            payload[f"z1p{day_key}{i}time"] = entry["time"]
            payload[f"z1p{day_key}{i}temp"] = entry["temp"]

        await self._async_post(payload, url=URL_SET_SCHEDULE)
        _LOGGER.info("Salus IT500 set_schedule OK")


@dataclass
class SalusFirstLoginResponseItem:
    """Representation of a Salus Thermostat device received from the first login response."""

    devId: str = ""
    devName: str = ""
    devToken: str | None = None


class SalusThermostat(CoordinatorEntity, ClimateEntity):
    """Representation of a Salus Thermostat device."""

    _attr_icon = "mdi:thermostat"

    def __init__(self, name, device_id: str, coordinator: SalusDataUpdateCoordinator):
        super().__init__(coordinator)
        self._name = name
        self._id = device_id

    @property
    def supported_features(self):
        return SUPPORT_FLAGS

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self) -> str:
        return f"{self._id}_climate"

    @property
    def should_poll(self):
        return False

    @property
    def min_temp(self):
        return MIN_TEMP

    @property
    def max_temp(self):
        return MAX_TEMP

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

    @property
    def current_temperature(self):
        data = self.coordinator.data or {}
        return data.get("current_temperature")

    @property
    def target_temperature(self):
        data = self.coordinator.data or {}
        return data.get("target_temperature")

    @property
    def hvac_mode(self):
        data = self.coordinator.data or {}
        climate_mode = data.get("operation_mode")
        if climate_mode == "ON":
            return HVACMode.HEAT
        return HVACMode.OFF

    @property
    def hvac_modes(self):
        return [HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_action(self):
        data = self.coordinator.data or {}
        if data.get("status") == "ON":
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_mode(self):
        data = self.coordinator.data or {}
        return data.get("status")

    @property
    def preset_modes(self):
        return SUPPORT_PRESET

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        schedule = data.get("schedule", {})
        return {
            "schedule_z1_mon": schedule.get("Mon", []),
            "schedule_z1_tus": schedule.get("Tus", []),
            "schedule_z1_wed": schedule.get("Wed", []),
            "schedule_z1_thu": schedule.get("Thu", []),
            "schedule_z1_fri": schedule.get("Fri", []),
            "schedule_z1_sat": schedule.get("Sat", []),
            "schedule_z1_sun": schedule.get("Sun", []),
        }

    async def async_set_temperature(self, **kwargs):
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self.coordinator.async_set_temperature(temperature)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac_mode):
        await self.coordinator.async_set_hvac_mode(hvac_mode)
        await self.coordinator.async_request_refresh()

    @staticmethod
    def _normalize_schedule_entries(entries: list[dict]) -> list[dict]:
        normalized = []
        used_times = set()

        for entry in entries:
            time_str = entry.get("time")
            temp = entry.get("temp")
            if not time_str or temp is None:
                continue
            if time_str in used_times:
                continue
            used_times.add(time_str)
            normalized.append({"time": time_str, "temp": float(temp)})

        minute = 0
        while len(normalized) < 6:
            hh = minute // 60
            mm = minute % 60
            time_str = f"{hh:02d}:{mm:02d}"
            if time_str not in used_times:
                used_times.add(time_str)
                normalized.append({"time": time_str, "temp": normalized[-1]["temp"] if normalized else 5.0})
            minute += 1

        return normalized

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": "Salus Thermostat",
            "manufacturer": "Salus",
            "model": "iT500",
        }
