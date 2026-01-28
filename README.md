# Salus iT500 (salus-it500.com) — Home Assistant Custom Integration

Custom integration for the **Salus iT500** thermostat system that uses the **salus-it500.com** cloud portal.

- Configurable via the Home Assistant UI (config flow)
- Supports multiple devices on the same Salus account
- Exposes climate + frost temperature + per-day schedule entities

## Features

- **Climate entity**
	- Current temperature and target setpoint
	- Turn heating **on/off** (maps to the portal “Auto” toggle)
	- Set target temperature
- **Frost temperature** (Number entity)
- **Weekly schedule**
	- Exposes one Text entity per day (Mon–Sun)
	- Optional service call to set a day schedule

## Installation

### HACS (recommended)

1. HACS → **Integrations** → menu (⋮) → **Custom repositories**
2. Add this repository URL
3. Category: **Integration**
4. Install
5. Restart Home Assistant

### Manual

Copy the folder `custom_components/salus_it500` into your Home Assistant `config/custom_components/` directory.

## Configuration

1. Home Assistant → **Settings** → **Devices & services**
2. **Add integration** → search for **Salus IT500**
3. Enter:
	 - **Username** (your salus-it500.com email)
	 - **Password**
	 - Optional **Name**

The integration will log in and create entities for each device found on your account.

## Entities

Per device, the integration creates:

- `climate` — thermostat control
- `number` — frost temperature
- `text` (7 entities) — schedule for each day: `Schedule Mon`, `Schedule Tue`, …

## Schedule format

Each daily schedule is a space-separated list of `HH:MM/TEMP` tokens.

Example:

`06:30/21 08:30/18 17:00/21 23:00/16`

Notes:

- Times must be `HH:MM` (24h)
- Temperature can be integer or decimal
- The device supports up to **6 entries per day** (extra entries are ignored)

## Service

The integration registers the service:

- `salus_it500.set_schedule`

Service data:

- `device_id` (string, required)
- `day` (string, required; e.g. `Mon`, `Tue`, …)
- `entries` (list, required; each entry like `{ "time": "06:30", "temp": 21 }`)

## Polling / iot_class

This integration uses **cloud polling** to fetch state updates from salus-it500.com.
Commands are sent to the same portal endpoints.

## Troubleshooting

- If you see authentication/token errors, verify your portal credentials and that salus-it500.com is reachable from your Home Assistant instance.
- If entities don’t appear, restart Home Assistant after installing/updating.

## Disclaimer

This is an **unofficial** integration and is not affiliated with Salus.
The Salus cloud portal may change at any time, which can break this integration.
