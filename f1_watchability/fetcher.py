"""
Fetches and transforms OpenF1 API data into internal models.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from . import api
from .models import (
    ChampionshipEntry,
    DriverResult,
    PitStop,
    RaceControlEvent,
    SessionInfo,
    SessionRawData,
    WeatherSnapshot,
)

logger = logging.getLogger(__name__)

SESSION_TYPE_MAP = {
    "Race": "Race",
    "Qualifying": "Qualifying",
    "Sprint": "Sprint",
    "Sprint Qualifying": "Sprint Qualifying",
    "Sprint Shootout": "Sprint Qualifying",
}

SCORABLE_TYPES = set(SESSION_TYPE_MAP.values())


def get_scorable_sessions(year: int) -> list[SessionInfo]:
    raw_sessions = api.get_sessions(year)

    # Build a meeting_key → meeting_name map from the meetings endpoint,
    # since the sessions endpoint sometimes returns null for meeting_name.
    raw_meetings = api.get_meetings(year)
    meeting_names: dict[int, str] = {
        m["meeting_key"]: m["meeting_name"]
        for m in raw_meetings
        if m.get("meeting_key") and m.get("meeting_name")
    }

    result = []
    for s in raw_sessions:
        canonical = SESSION_TYPE_MAP.get(s.get("session_type", ""))
        if not canonical:
            continue
        meeting_key = s.get("meeting_key", 0)
        # Prefer meetings endpoint name, fall back to sessions endpoint, then default
        meeting_name = (
            meeting_names.get(meeting_key)
            or s.get("meeting_name")
            or s.get("location")
            or "Unknown GP"
        )
        result.append(SessionInfo(
            session_key=s["session_key"],
            session_name=s.get("session_name", canonical),
            session_type=canonical,
            meeting_name=meeting_name,
            circuit_short_name=s.get("circuit_short_name", s.get("location", "Unknown")),
            date_start=s.get("date_start", ""),
            year=year,
            meeting_key=meeting_key,
        ))
    return result


def get_meetings_with_sessions(year: int) -> dict[int, list[SessionInfo]]:
    """Group scorable sessions by meeting_key, preserving session order."""
    sessions = get_scorable_sessions(year)
    grouped: dict[int, list[SessionInfo]] = {}
    for s in sessions:
        grouped.setdefault(s.meeting_key, []).append(s)
    return grouped


def fetch_grid_positions(qualifying_session_key: int) -> dict[int, int]:
    """
    Returns {driver_number: grid_position} from a qualifying session's
    final position snapshot. Used to enrich race DriverResult objects.
    """
    raw_position = api.get_position(qualifying_session_key)
    final: dict[int, int] = {}
    for p in raw_position:
        num = p.get("driver_number")
        pos = p.get("position")
        if num is not None and pos is not None:
            final[num] = pos
    return final


def fetch_session_data(session_info: SessionInfo, grid_positions: dict[int, int] | None = None) -> SessionRawData:
    sk = session_info.session_key
    session_type = session_info.session_type

    raw_drivers = api.get_drivers(sk)
    raw_pits = api.get_pit_stops(sk)
    raw_rc = api.get_race_control(sk)
    raw_weather = api.get_weather(sk)
    raw_position = api.get_position(sk)
    raw_intervals = api.get_intervals(sk)
    raw_laps = api.get_laps(sk)

    # Championship standings — only available for race-type sessions
    championship_before: list[ChampionshipEntry] = []
    championship_after: list[ChampionshipEntry] = []
    if session_type in ("Race", "Sprint"):
        raw_champ = api.get_championship_drivers(sk)
        for entry in raw_champ:
            driver_num = entry.get("driver_number")
            if driver_num is None:
                continue
            ce = ChampionshipEntry(
                driver_number=driver_num,
                full_name=entry.get("full_name", f"Driver #{driver_num}"),
                team_name=entry.get("team_name", "Unknown"),
                points=float(entry.get("points", 0) or 0),
                position=int(entry.get("position", 99) or 99),
            )
            # OpenF1 returns both start and end standings in the same endpoint
            # keyed by "meeting_key" context; we differentiate by points_before/points fields
            # For simplicity we store both using position_start vs position fields
            if entry.get("position_start") is not None:
                before = ChampionshipEntry(
                    driver_number=driver_num,
                    full_name=ce.full_name,
                    team_name=ce.team_name,
                    points=float(entry.get("points_before", 0) or 0),
                    position=int(entry.get("position_start", 99) or 99),
                )
                championship_before.append(before)
            championship_after.append(ce)

    # Build driver number → info map
    driver_map: dict[int, dict] = {
        d["driver_number"]: d for d in raw_drivers if d.get("driver_number") is not None
    }

    # Final position per driver (last snapshot wins)
    final_positions: dict[int, int] = {}
    for p in raw_position:
        num = p.get("driver_number")
        pos = p.get("position")
        if num is not None and pos is not None:
            final_positions[num] = pos

    drivers: list[DriverResult] = []
    for num, pos in final_positions.items():
        d = driver_map.get(num, {})
        drivers.append(DriverResult(
            driver_number=num,
            full_name=d.get("full_name", f"Driver #{num}"),
            team_name=d.get("team_name", "Unknown"),
            finish_position=pos,
            grid_position=(grid_positions or {}).get(num, 0),
        ))
    drivers.sort(key=lambda x: x.finish_position)

    pit_stops = [
        PitStop(
            driver_number=p["driver_number"],
            lap_number=p.get("lap_number", 0),
            pit_duration=p.get("pit_duration"),
        )
        for p in raw_pits if p.get("driver_number") is not None
    ]

    rc_events = [
        RaceControlEvent(
            category=msg.get("category", "Other"),
            message=msg.get("message", ""),
            lap_number=msg.get("lap_number"),
        )
        for msg in raw_rc
    ]

    weather_samples = [
        WeatherSnapshot(
            rainfall=float(w.get("rainfall", 0) or 0),
            track_temperature=float(w.get("track_temperature", 0) or 0),
            air_temperature=float(w.get("air_temperature", 0) or 0),
            wind_speed=float(w.get("wind_speed", 0) or 0),
        )
        for w in raw_weather
    ]

    return SessionRawData(
        session=session_info,
        drivers=drivers,
        pit_stops=pit_stops,
        race_control=rc_events,
        weather_samples=weather_samples,
        position_data=raw_position,
        lap_data=raw_laps,
        interval_data=raw_intervals,
        championship_before=championship_before,
        championship_after=championship_after,
    )
