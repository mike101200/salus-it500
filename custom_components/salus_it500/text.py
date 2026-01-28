"""Text platform for Salus integration (schedule as text)."""
import logging
import re
import asyncio

from homeassistant.components.text import TextEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import (
    DOMAIN,
    DEFAULT_NAME,
    CONF_NAME,
    SCHEDULE_DAYS,
    SCAN_INTERVAL,
    SIGNAL_NEW_DEVICE,
)
from .climate import SalusDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Salus Text platform from config entry."""
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)

    coordinators = hass.data.setdefault(DOMAIN, {})
    known_ids = set(coordinators.keys())

    entities = []
    for device_id, coordinator in coordinators.items():
        entities.extend(SalusDayScheduleText(name, device_id, coordinator, day) for day in SCHEDULE_DAYS)
    if entities:
        async_add_entities(entities)

    async def _add_device(device_id: str) -> None:
        if device_id in known_ids:
            return
        coord = hass.data.get(DOMAIN, {}).get(device_id)
        if not coord:
            return
        async_add_entities([SalusDayScheduleText(name, device_id, coord, day) for day in SCHEDULE_DAYS])
        known_ids.add(device_id)

    def _on_new_device(device_id: str) -> None:
        # Dispatcher may call from a non-event-loop thread -> hop to HA loop safely
        def _schedule() -> None:
            hass.async_create_task(_add_device(device_id))

        hass.loop.call_soon_threadsafe(_schedule)

    async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _on_new_device)

    async def _scan_for_new(now):
        coordinators = hass.data.get(DOMAIN, {})
        for device_id in set(coordinators.keys()) - known_ids:
            await _add_device(device_id)

    async_track_time_interval(hass, _scan_for_new, SCAN_INTERVAL)


class SalusDayScheduleText(CoordinatorEntity, TextEntity):
    """Expose a single day's schedule as a text entity."""

    _attr_native_max_length = 2048

    def __init__(self, name, device_id: str, coordinator: SalusDataUpdateCoordinator, day: str):
        super().__init__(coordinator)
        self._name = f"Schedule {day}"
        self._id = device_id
        self._day = day

    @property
    def name(self) -> str:
        return self._name

    @property
    def unique_id(self) -> str:
        return f"{self._id}_schedule_{self._day.lower()}"

    @property
    def native_value(self) -> str:
        data = self.coordinator.data or {}
        schedule = data.get("schedule", {})
        day_entries = schedule.get(self._day, [])
        return " ".join(f'{e["time"]}/{e["temp"]}' for e in day_entries)

    async def async_set_value(self, value: str) -> None:
        try:
            tokens = [t for t in value.split(" ") if t.strip()]
            entries = []
            for token in tokens:
                if "/" not in token:
                    raise ValueError(f"Invalid token: {token}")
                time_str, temp_str = token.split("/", 1)
                if not re.match(r"^\d{2}:\d{2}$", time_str):
                    raise ValueError(f"Invalid time: {time_str}")
                temp = float(re.sub(r"[^\d.]", "", temp_str))
                entries.append({"time": time_str, "temp": temp})
        except ValueError as err:
            _LOGGER.error("Invalid schedule format for %s: %s", self._day, err)
            raise ValueError("Invalid schedule format") from err

        await self.coordinator.async_set_schedule(self._day, entries)
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": "Salus Thermostat",
            "manufacturer": "Salus",
            "model": "iT500",
        }
