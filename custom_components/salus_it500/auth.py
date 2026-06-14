"""Shared authentication helpers for Salus IT500 integration."""
import asyncio
import logging
import re

import aiohttp
import async_timeout
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import URL_LOGIN

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


async def login(session: aiohttp.ClientSession, username: str, password: str):
    """Perform the full login flow (GET page + POST credentials).

    Returns (success: bool, error_message: str | None).
    On success the session cookie jar holds the authenticated session.
    On failure returns False with a human-readable error.
    """
    payload = {
        "IDemail": username,
        "password": password,
        "login": "Login",
        "keep_logged_in": "1",
    }

    try:
        # Step 1: GET login page to establish PHP session cookie
        async with async_timeout.timeout(REQUEST_TIMEOUT):
            async with session.get(URL_LOGIN, headers=BROWSER_HEADERS) as resp:
                resp.raise_for_status()

        # Step 2: POST login with browser-like headers
        login_headers = {**BROWSER_HEADERS}
        login_headers["Content-Type"] = "application/x-www-form-urlencoded"
        login_headers["Origin"] = "https://salus-it500.com"
        login_headers["Referer"] = URL_LOGIN

        async with async_timeout.timeout(REQUEST_TIMEOUT):
            async with session.post(
                URL_LOGIN, data=payload, headers=login_headers
            ) as resp:
                resp.raise_for_status()
                post_text = await resp.text()

                # Server returns 200 + login form on bad credentials (no 302 redirect)
                if resp.status == 200 and 'id="login_form"' in post_text:
                    error_match = re.search(
                        r'class="errorMessage">(.*?)</', post_text
                    )
                    error_msg = (
                        error_match.group(1).strip().rstrip("<br/>")
                        if error_match
                        else "Login failed"
                    )
                    masked_password = (
                        f"{password[:2]}****{password[-2:]}"
                        if len(password) > 4
                        else "****"
                    )
                    return False, (
                        f"Login failed for user {username} "
                        f"with password {masked_password}: {error_msg}"
                    )

        return True, None

    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        return False, f"Connection error: {err}"


def _mask_password(password: str) -> str:
    """Mask a password for safe logging."""
    if len(password) > 4:
        return f"{password[:2]}****{password[-2:]}"
    return "****"
