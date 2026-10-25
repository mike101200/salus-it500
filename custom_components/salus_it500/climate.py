"""
Adds support for the Salus Thermostat units.

Talks to the Arrayent cloud API used by the official Android app
(see api.py). The old salus-it500.com web site endpoints are gone.
"""
import logging
from dataclasses import dataclass

import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.components.climate.const import (
    HVACAction,
    HVACMode,
    ClimateEntityFeature,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_PASSWORD,
    CONF_USERNAME,
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
    MIN_TEMP,
    MAX_TEMP,
    SCAN_INTERVAL,
    NEWDEVICE_SCAN_INTERVAL,
    SCHEDULE_DAYS,
    SCHEDULE_SERVICE,
    SIGNAL_NEW_DEVICE,
)
from .api import (
    SalusApiError,
    SalusClient,
    ATR_ZONE1_ROOM_TEMP,
    ATR_ZONE1_TARGET_TEMP,
    ATR_ZONE1_HEAT_STATUS,
    ATR_ZONE1_OFF_MODE,
    ATR_ZONE1_MANUAL_SETPOINT,
    ATR_SYSTEM_FROST_TEMP,
    ZONE1_SCHEDULE_ATTRS,
    cctt_decode,
    cctt_encode,
)

try:
    from homeassistant.components.climate import ClimateEntity
except ImportError:
    from homeassistant.components.climate import ClimateDevice as ClimateEntity

_LOGGER = logging.getLogger(__name__)

SUPPORT_FLAGS = ClimateEntityFeature.TARGET_TEMPERATURE
SUPPORT_PRESET = []


def _to_cents(value: str) -> float | None:
    """Convert an x0.01 C attribute value (e.g. '2300') to degrees."""
    value = (value or "").strip()
    if not value:
        return None
    return int(value) / 100.0


def _build_schedule_from_attrs(attrs: dict) -> dict:
    """Decode the per-day CCTT program strings into entry lists."""
    schedule = {}
    for day, attr in ZONE1_SCHEDULE_ATTRS.items():
        schedule[day] = cctt_decode(attrs.get(attr, ""))
    return schedule


async def async_first_login(coordinator) -> list:
    """Authenticate and retrieve the device list from the cloud API."""
    try:
        devices = await coordinator._client.get_devices()
    except SalusApiError as err:
        raise UpdateFailed(err) from err

    if not devices:
        raise UpdateFailed("No devices found for this account")

    _LOGGER.info("Salus IT500 cloud login and get_devices OK (%d device(s))", len(devices))
    return devices


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
    """Coordinator to manage Salus IT500 data polling via the cloud API."""

    def __init__(self, hass, username: str, password: str, device_id: str):
        self._username = username
        self._password = password
        self._device_id = device_id
        self._type_id = "1"
        self._client = SalusClient(async_get_clientsession(hass), username, password)

        super().__init__(
            hass=hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict:
        return await self._async_get_data()

    async def _async_get_data(self) -> dict:
        try:
            if not self._client.sec_token:
                await self._client.authenticate()
            attrs = await self._client.get_device_attributes(self._device_id, self._type_id)
        except SalusApiError as err:
            raise UpdateFailed(err) from err

        current = _to_cents(attrs.get(ATR_ZONE1_ROOM_TEMP))
        target = _to_cents(attrs.get(ATR_ZONE1_TARGET_TEMP))

        if target is None:
            # fall back to the manual-mode setpoint
            target = _to_cents(attrs.get(ATR_ZONE1_MANUAL_SETPOINT))

        return {
            "target_temperature": target,
            "current_temperature": current,
            "frost": _to_cents(attrs.get(ATR_SYSTEM_FROST_TEMP)),
            "status": "ON" if attrs.get(ATR_ZONE1_HEAT_STATUS) == "1" else "OFF",
            "operation_mode": "OFF" if attrs.get(ATR_ZONE1_OFF_MODE) == "1" else "ON",
            "schedule": _build_schedule_from_attrs(attrs),
        }

    async def _set_attrs(self, attrs: dict) -> None:
        try:
            await self._client.set_device_attributes(self._device_id, attrs)
        except SalusApiError as err:
            raise UpdateFailed(err) from err

    async def async_set_temperature(self, temperature: float) -> None:
        await self._set_attrs({ATR_ZONE1_TARGET_TEMP: str(int(round(temperature * 100)))})
        _LOGGER.info("Salus IT500 set_temperature OK")

    async def async_set_frost(self, frost: float) -> None:
        await self._set_attrs({ATR_SYSTEM_FROST_TEMP: str(int(round(frost * 100)))})
        _LOGGER.info("Salus IT500 set_frost OK")

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        # A89: CH1 Off Mode (1) / running (0)
        off = "1" if hvac_mode == HVACMode.OFF else "0"
        await self._set_attrs({ATR_ZONE1_OFF_MODE: off})
        _LOGGER.info("Salus IT500 set_hvac_mode OK")

    async def async_set_schedule(self, day: str, entries: list[dict]) -> None:
        day = day.capitalize()
        if day not in SCHEDULE_DAYS:
            raise ValueError("Invalid day")

        entries = SalusThermostat._normalize_schedule_entries(entries)
        program = cctt_encode(entries)
        await self._set_attrs({ZONE1_SCHEDULE_ATTRS[day]: program})
        _LOGGER.info("Salus IT500 set_schedule OK")


@dataclass
class SalusFirstLoginResponseItem:
    """Representation of a Salus Thermostat device received from the device list."""

    devId: str = ""
    devName: str = ""
    devTypeId: str = "1"


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
