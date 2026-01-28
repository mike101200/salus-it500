"""Number platform for Salus integration (frost temperature)."""
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.number import NumberEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import asyncio

from . import DOMAIN, DEFAULT_NAME, CONF_NAME, MIN_TEMP, MAX_TEMP, SCAN_INTERVAL, SIGNAL_NEW_DEVICE
from .climate import SalusDataUpdateCoordinator


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Salus Number platform from config entry."""
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)

    coordinators = hass.data.setdefault(DOMAIN, {})
    known_ids = set(coordinators.keys())

    entities = [SalusFrostNumber(name, device_id, coord) for device_id, coord in coordinators.items()]
    if entities:
        async_add_entities(entities)

    async def _add_device(device_id: str) -> None:
        if device_id in known_ids:
            return
        coord = hass.data.get(DOMAIN, {}).get(device_id)
        if not coord:
            return
        async_add_entities([SalusFrostNumber(name, device_id, coord)])
        known_ids.add(device_id)

    def _on_new_device(device_id: str) -> None:
        def _schedule() -> None:
            hass.async_create_task(_add_device(device_id))

        hass.loop.call_soon_threadsafe(_schedule)

    async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _on_new_device)

    async def _scan_for_new(now):
        coordinators = hass.data.get(DOMAIN, {})
        for device_id in set(coordinators.keys()) - known_ids:
            await _add_device(device_id)

    async_track_time_interval(hass, _scan_for_new, SCAN_INTERVAL)


class SalusFrostNumber(CoordinatorEntity, NumberEntity):
    """Expose frost temperature as a number entity."""

    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = 0.5
    _attr_icon = "mdi:snowflake-thermometer"

    def __init__(self, name, device_id: str, coordinator: SalusDataUpdateCoordinator):
        super().__init__(coordinator)
        self._name = f"Frost temperature"
        self._id = device_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return f"{self._id}_frost"

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        return data.get("frost")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_frost(value)
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": "Salus Thermostat",
            "manufacturer": "Salus",
            "model": "IT500",
        }
