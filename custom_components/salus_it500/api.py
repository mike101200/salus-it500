"""Arrayent cloud API client for the Salus IT500.

This mirrors the API used by the official Salus IT500 Android app
(decompiled sources in salus-it500-sources/):

  * Login (V2):  POST {host}/acc/applications/{appName}/sessions
                 body: {"username": ..., "password": md5(plain password)}
                 resp: {"userId": ..., "securityToken": ...}
  * Devices (V1): GET {host}/zdk/services/zamapi/<endpoint>?secToken=...
                 - getDeviceList?secToken=...&userId=...
                 - getDeviceAttributesWithValues?secToken=...&deviceTypeId=...&devId=...
                 - setMultiDeviceAttributes2?secToken=...&devId=...&name1=...&value1=...

The app sends the MD5 (lowercase hex) of the plaintext password and,
on failure, retries with the MD5 of the password stripped of all
non-alphanumeric characters.
"""
import asyncio
import hashlib
import logging
import re
import xml.etree.ElementTree as ET

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20

CLOUD_HOST = "https://sal-emea-p01-api.arrayent.com"
URL_SESSIONS = f"{CLOUD_HOST}/acc/applications/SalusService/sessions"
URL_ZDMAPI = f"{CLOUD_HOST}/zdk/services/zamapi/"

APP_NAME = "SalusService"

# --- Zone 1 (CH1) attribute names, from the app's Zone1Atr/attributes ---
ATR_ZONE1_ROOM_TEMP = "A84"        # CH1 Current Room Temperature (x 0.01 C)
ATR_ZONE1_TARGET_TEMP = "A85"      # CH1 Current Setpoint (x 0.01 C)
ATR_ZONE1_SCHEDULE_TYPE = "A86"    # CH1 Schedule Type
ATR_ZONE1_HEAT_STATUS = "A87"      # CH1 Relay On/Off Status
ATR_ZONE1_RUNNING_MODE = "A88"     # CH1 Auto Mode (0) / Temp Hold (1)
ATR_ZONE1_OFF_MODE = "A89"         # CH1 Off Mode (1)
ATR_ZONE1_FROST_STATE = "A90"      # CH1 Frost Active
ATR_ZONE1_BOOST_HOURS = "A91"      # CH1 Boost Remaining hours
ATR_ZONE1_MANUAL_MODE = "A92"      # CH1 Manual Mode (1)
ATR_ZONE1_MANUAL_SETPOINT = "A93"  # CH1 Manual Mode Setpoint (x 0.01 C)

ATR_SYSTEM_FROST_TEMP = "S09"      # Frost Temperature (x 0.01 C)

ZONE1_SCHEDULE_ATTRS = {
    "Mon": "A00",
    "Tus": "A01",
    "Wed": "A02",
    "Thu": "A03",
    "Fri": "A04",
    "Sat": "A05",
    "Sun": "A06",
}

# --- CCTT schedule string encoding (app: TemperatureUtils + arrays.xml) ---
CCTT_HOURS = "0123456789:;<=>?@ABCDEFG"          # 24 chars, index = hour
CCTT_TEMP = "0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[]^-'abcdefghijk"  # 59 chars


class SalusApiError(Exception):
    """Raised when the Arrayent cloud API returns an error."""


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def cctt_encode_entry(hour: int, minute: int, temp: float) -> str:
    """Encode one schedule entry (4 chars) in CCTT format."""
    whole = int(temp)
    frac = int(round((temp - whole) * 10))  # 0 or 5 for .0 / .5
    if not (0 <= hour < 24 and 0 <= minute < len(CCTT_TEMP)):
        raise ValueError(f"Invalid schedule time {hour:02d}:{minute:02d}")
    if not (0 <= whole < len(CCTT_TEMP) and 0 <= frac < len(CCTT_TEMP)):
        raise ValueError(f"Invalid schedule temperature {temp}")
    return CCTT_HOURS[hour] + CCTT_TEMP[minute] + CCTT_TEMP[whole] + CCTT_TEMP[frac]


def cctt_encode(entries: list[dict]) -> str:
    """Encode a list of {"time": "HH:MM", "temp": float} into a CCTT string."""
    out = []
    for entry in entries:
        hour, minute = (int(p) for p in entry["time"].split(":"))
        out.append(cctt_encode_entry(hour, minute, float(entry["temp"])))
    return "".join(out)


def cctt_decode(program: str) -> list[dict]:
    """Decode a CCTT program string into [{"time": "HH:MM", "temp": float}]."""
    entries = []
    program = program.strip()
    for i in range(0, len(program) - 3, 4):
        h, m, w, f = program[i:i + 4]
        try:
            hour = CCTT_HOURS.index(h)
            minute = CCTT_TEMP.index(m)
            whole = CCTT_TEMP.index(w)
            frac = CCTT_TEMP.index(f)
        except ValueError:
            _LOGGER.debug("Salus IT500: unparsable CCTT chunk %r", program[i:i + 4])
            break
        # a 00:00/0.0 entry marks "no more events" in the app
        if hour == 0 and minute == 0 and whole == 0 and frac == 0:
            break
        entries.append({
            "time": f"{hour:02d}:{minute:02d}",
            "temp": whole + frac / 10.0,
        })
    return entries


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_xml_flat(xml_text: str) -> dict:
    """Parse an Arrayent V1 XML response into a flat dict of local tag -> text.

    Repeated tags (e.g. attrList/devList) are collected by the caller through
    parse_xml_repeated().
    """
    root = ET.fromstring(xml_text)
    return {_strip_ns(el.tag): (el.text or "").strip() for el in root.iter()}


def parse_xml_repeated(xml_text: str, item_tag: str) -> list[dict]:
    """Parse an Arrayent V1 XML response into a list of dicts, one per
    occurrence of <item_tag>...</item_tag>."""
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        if _strip_ns(el.tag) == item_tag:
            items.append({_strip_ns(c.tag): (c.text or "").strip() for c in el})
    return items


def _check_fault(payload: dict, endpoint: str):
    """Arrayent V1 faults arrive as <Fault><code>..</code><reason>..</reason>."""
    if "code" in payload and "reason" in payload:
        raise SalusApiError(f"{endpoint} failed: [{payload['code']}] {payload['reason']}")


class SalusClient:
    """Async client for the Salus IT500 Arrayent cloud API."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str):
        self._session = session
        self._username = username
        self._password = password
        self._user_id: int | None = None
        self._sec_token: str | None = None

    @property
    def user_id(self) -> int | None:
        return self._user_id

    @property
    def sec_token(self) -> str | None:
        return self._sec_token

    async def _request(self, method: str, url: str, **kwargs) -> bytes:
        try:
            async with self._session.request(method, url, **kwargs) as resp:
                body = await asyncio.wait_for(resp.read(), timeout=REQUEST_TIMEOUT)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SalusApiError(f"Connection error calling {url}: {err}") from err
        if resp.status >= 400:
            snippet = body[:300].decode("utf-8", "replace")
            raise SalusApiError(f"HTTP {resp.status} from {url}: {snippet}")
        return body

    async def authenticate(self) -> tuple[int, str]:
        """Log in and cache userId + securityToken. Mirrors the app's retry:
        first MD5(plain), then MD5(plain with non-alphanumerics removed)."""
        if self._sec_token:
            return self._user_id, self._sec_token

        candidates = [md5_hex(self._password)]
        stripped = re.sub(r"[^a-zA-Z0-9]", "", self._password)
        if stripped and stripped != self._password:
            candidates.append(md5_hex(stripped))

        last_err: Exception | None = None
        for candidate in candidates:
            try:
                body = await self._request(
                    "POST",
                    URL_SESSIONS,
                    json={"username": self._username, "password": candidate},
                    headers={"Content-Type": "application/json"},
                )
                import json as _json

                data = _json.loads(body)
                self._user_id = int(data["userId"])
                self._sec_token = str(data["securityToken"])
                _LOGGER.info("Salus IT500 cloud login OK (user %s)", self._user_id)
                return self._user_id, self._sec_token
            except (SalusApiError, KeyError, ValueError) as err:
                last_err = err
                _LOGGER.debug("Salus IT500 login attempt failed: %s", err)
        masked = (
            f"{self._password[:2]}****{self._password[-2:]}"
            if len(self._password) > 4
            else "****"
        )
        raise SalusApiError(
            f"Login failed for user {self._username} with password {masked}: {last_err}"
        )

    async def _ensure_token(self) -> str:
        _, token = await self.authenticate()
        return token

    async def get_devices(self) -> list[dict]:
        """Return [{"devId", "devName", "typeId"}] for the logged-in user."""
        token = await self._ensure_token()
        url = (
            f"{URL_ZDMAPI}getDeviceList"
            f"?secToken={token}&userId={self._user_id}"
        )
        body = await self._request("GET", url)
        devices = parse_xml_repeated(body.decode("utf-8"), "devList")
        return [
            {
                "devId": d.get("devId", ""),
                "devName": re.sub(r"\s+", " ", d.get("devName", "")),
                "typeId": d.get("typeId", "1"),
            }
            for d in devices
            if d.get("devId")
        ]

    async def get_device_attributes(self, device_id: str, type_id: str = "1") -> dict:
        """Return {attributeName: value} for one device."""
        token = await self._ensure_token()
        url = (
            f"{URL_ZDMAPI}getDeviceAttributesWithValues"
            f"?secToken={token}&deviceTypeId={type_id}&devId={device_id}"
        )
        body = await self._request("GET", url)
        attrs: dict[str, str] = {}
        for item in parse_xml_repeated(body.decode("utf-8"), "attrList"):
            name = item.get("name")
            if name:
                attrs[name] = item.get("value", "")
        return attrs

    async def set_device_attributes(self, device_id: str, attrs: dict) -> None:
        """Set attributes via setMultiDeviceAttributes2 (GET, like the app)."""
        if not attrs:
            return
        token = await self._ensure_token()
        params: dict[str, str] = {"secToken": token, "devId": device_id}
        for i, (name, value) in enumerate(attrs.items(), start=1):
            params[f"name{i}"] = name
            params[f"value{i}"] = str(value)
        body = await self._request("GET", f"{URL_ZDMAPI}setMultiDeviceAttributes2", params=params)
        payload = parse_xml_flat(body.decode("utf-8"))
        _check_fault(payload, "setMultiDeviceAttributes2")
        _LOGGER.info("Salus IT500 set attributes OK: %s", attrs)
