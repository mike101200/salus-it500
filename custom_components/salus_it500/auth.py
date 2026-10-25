"""Shared authentication helpers for Salus IT500 integration.

The old salus-it500.com web site is gone; authentication now goes through
the same Arrayent cloud API the Android app uses (see api.py).
"""
import logging

import aiohttp

from .api import SalusApiError, SalusClient

_LOGGER = logging.getLogger(__name__)


async def login(session: aiohttp.ClientSession, username: str, password: str):
    """Authenticate against the Salus cloud API.

    Returns (success: bool, error_message: str | None).
    """
    client = SalusClient(session, username, password)
    try:
        await client.authenticate()
        return True, None
    except SalusApiError as err:
        return False, str(err)
