"""The Salus IT500 component."""
from datetime import timedelta

import homeassistant.helpers.config_validation as cv

__version__ = "0.0.9"

DOMAIN = "salus_it500"
PLATFORMS = ["climate", "text", "number"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

DEFAULT_NAME = "Salus IT500"
CONF_NAME = "name"

URL_LOGIN = "https://salus-it500.com/public/login.php"
URL_GET_DEVICES = "https://salus-it500.com/public/devices.php"
URL_GET_TOKEN = "https://salus-it500.com/public/control.php"
URL_GET_DATA = "https://salus-it500.com/public/ajax_device_values.php"
URL_SET_DATA = "https://salus-it500.com/includes/set.php"
URL_SET_SCHEDULE = "https://salus-it500.com/includes/program.php"

# Values from web interface
MIN_TEMP = 5
MAX_TEMP = 34.5

SCAN_INTERVAL = timedelta(minutes=5)  # poll every 5 minutes
NEWDEVICE_SCAN_INTERVAL = timedelta(hours=5)  # poll every 5 hours

# Schedule support
SCHEDULE_DAYS = ["Mon", "Tus", "Wed", "Thu", "Fri", "Sat", "Sun"]
SCHEDULE_SERVICE = "set_schedule"
SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"


async def async_setup(hass, config):
    """Set up the Salus integration."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass, entry):
    """Set up Salus from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass, entry):
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
