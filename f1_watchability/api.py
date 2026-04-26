"""
OpenF1 API client — thin wrapper around https://openf1.org/
"""

from __future__ import annotations

import time
import logging
from typing import Any
from urllib.parse import urlencode

import requests

BASE_URL = "https://api.openf1.org/v1"
logger = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({"User-Agent": "f1-watchability/0.1"})


def _get(endpoint: str, params: dict[str, Any] | None = None, retries: int = 3) -> list[dict]:
    url = f"{BASE_URL}/{endpoint}"
    if params:
        url += "?" + urlencode(params)
    for attempt in range(retries):
        try:
            logger.debug(f"GET {url}")
            resp = _session.get(url, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            wait = 1.5 ** attempt
            logger.debug(f"Retrying in {wait:.1f}s ({e})")
            time.sleep(wait)
    return []


def get_sessions(year: int) -> list[dict]:
    return _get("sessions", {"year": year})


def get_drivers(session_key: int) -> list[dict]:
    return _get("drivers", {"session_key": session_key})


def get_position(session_key: int) -> list[dict]:
    return _get("position", {"session_key": session_key})


def get_intervals(session_key: int) -> list[dict]:
    return _get("intervals", {"session_key": session_key})


def get_pit_stops(session_key: int) -> list[dict]:
    return _get("pit", {"session_key": session_key})


def get_race_control(session_key: int) -> list[dict]:
    return _get("race_control", {"session_key": session_key})


def get_weather(session_key: int) -> list[dict]:
    return _get("weather", {"session_key": session_key})


def get_laps(session_key: int) -> list[dict]:
    return _get("laps", {"session_key": session_key})


def get_stints(session_key: int) -> list[dict]:
    return _get("stints", {"session_key": session_key})


def get_championship_drivers(session_key: int) -> list[dict]:
    """Driver championship standings. Only available for race sessions."""
    return _get("championship_drivers", {"session_key": session_key})


def get_championship_teams(session_key: int) -> list[dict]:
    """Constructor championship standings. Only available for race sessions."""
    return _get("championship_teams", {"session_key": session_key})


def get_session_result(session_key: int) -> list[dict]:
    """Final classified results — works for Race, Sprint, and Qualifying sessions."""
    return _get("session_result", {"session_key": session_key})


def get_meetings(year: int) -> list[dict]:
    """All race meetings for a given year — reliable source of meeting_name."""
    return _get("meetings", {"year": year})
