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
    final classified results. Used to enrich race DriverResult objects.
    """
    raw = api.get_session_result(qualifying_session_key)
    if raw:
        # session_result gives us position directly
        final: dict[int, int] = {}
        for entry in raw:
            num = entry.get("driver_number")
            pos = entry.get("position")
            if num is not None and pos is not None:
                final[num] = int(pos)
        if final:
            return final

    # Fallback: infer from position endpoint
    raw_position = api.get_position(qualifying_session_key)
    final = {}
    for p in raw_position:
        num = p.get("driver_number")
        pos = p.get("position")
        if num is not None and pos is not None:
            final[num] = pos
    return final


def fetch_session_data(session_info: SessionInfo, grid_positions: dict[int, int] | None = None) -> SessionRawData:
    sk = session_info.session_key
    session_type = session_info.session_type
    is_race_type = session_type in ("Race", "Sprint")
    is_quali_type = session_type in ("Qualifying", "Sprint Qualifying")

    raw_drivers = api.get_drivers(sk)
    raw_rc = api.get_race_control(sk)
    raw_weather = api.get_weather(sk)

    # Race-specific endpoints
    raw_pits = []
    raw_position = []
    raw_intervals = []
    raw_laps = []
    championship_before: list[ChampionshipEntry] = []
    championship_after: list[ChampionshipEntry] = []

    if is_race_type:
        raw_pits = api.get_pit_stops(sk)
        raw_position = api.get_position(sk)
        raw_intervals = api.get_intervals(sk)
        raw_laps = api.get_laps(sk)

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
            if entry.get("position_start") is not None:
                championship_before.append(ChampionshipEntry(
                    driver_number=driver_num,
                    full_name=ce.full_name,
                    team_name=ce.team_name,
                    points=float(entry.get("points_before", 0) or 0),
                    position=int(entry.get("position_start", 99) or 99),
                ))
            championship_after.append(ce)

    # Build driver number → info map
    driver_map: dict[int, dict] = {
        d["driver_number"]: d for d in raw_drivers if d.get("driver_number") is not None
    }

    # ── Build driver results ──────────────────────────────────────────────────
    if is_quali_type:
        # Use session_result for qualifying — gives position + gap_to_leader
        drivers, interval_data = _build_quali_drivers(sk, driver_map)
    else:
        # Race: use position time-series for drivers, intervals for gaps
        final_positions: dict[int, int] = {}
        for p in raw_position:
            num = p.get("driver_number")
            pos = p.get("position")
            if num is not None and pos is not None:
                final_positions[num] = pos

        drivers = []
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
        interval_data = raw_intervals

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
        interval_data=interval_data,
        championship_before=championship_before,
        championship_after=championship_after,
    )


def _build_quali_drivers(
    session_key: int,
    driver_map: dict[int, dict],
) -> tuple[list[DriverResult], list[dict]]:
    """
    Build DriverResult list and synthetic interval_data from session_result
    for qualifying sessions, where the /intervals endpoint is not available.
    """
    raw_result = api.get_session_result(session_key)

    if not raw_result:
        # Fallback: use position endpoint if session_result is empty
        raw_position = api.get_position(session_key)
        final: dict[int, int] = {}
        for p in raw_position:
            num = p.get("driver_number")
            pos = p.get("position")
            if num is not None and pos is not None:
                final[num] = pos

        drivers = []
        for num, pos in final.items():
            d = driver_map.get(num, {})
            drivers.append(DriverResult(
                driver_number=num,
                full_name=d.get("full_name", f"Driver #{num}"),
                team_name=d.get("team_name", "Unknown"),
                finish_position=pos,
                grid_position=0,
            ))
        return sorted(drivers, key=lambda x: x.finish_position), []

    drivers = []
    interval_data = []

    for entry in raw_result:
        num = entry.get("driver_number")
        pos = entry.get("position")
        if num is None or pos is None:
            continue

        d = driver_map.get(num, {})
        drivers.append(DriverResult(
            driver_number=num,
            full_name=d.get("full_name", f"Driver #{num}"),
            team_name=d.get("team_name", "Unknown"),
            finish_position=int(pos),
            grid_position=0,
            is_classified=entry.get("dnf") is None and entry.get("dns") is None,
        ))

        # gap_to_leader in qualifying is an array [Q1_gap, Q2_gap, Q3_gap]
        # We want the best/last phase gap — take the last non-null value
        gap_raw = entry.get("gap_to_leader")
        gap = None
        if isinstance(gap_raw, list):
            # Take the last non-null phase
            for g in reversed(gap_raw):
                if g is not None:
                    try:
                        gap = float(g)
                        break
                    except (TypeError, ValueError):
                        pass
        elif gap_raw is not None:
            try:
                gap = float(gap_raw)
            except (TypeError, ValueError):
                pass

        if gap is not None:
            interval_data.append({
                "driver_number": num,
                "gap_to_leader": gap,
            })

    drivers.sort(key=lambda x: x.finish_position)
    return drivers, interval_data
