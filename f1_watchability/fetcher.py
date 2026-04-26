"""
Fetches and transforms OpenF1 API data into internal models.
"""

from __future__ import annotations
import logging
from . import api
from .models import (
    ChampionshipEntry, DriverResult, PitStop,
    RaceControlEvent, SessionInfo, SessionRawData, WeatherSnapshot,
)

logger = logging.getLogger(__name__)

SESSION_TYPE_MAP = {
    "Race": "Race",
    "Qualifying": "Qualifying",
    "Sprint": "Sprint",
    "Sprint Qualifying": "Sprint Qualifying",
    "Sprint Shootout": "Sprint Qualifying",
}

# session_name fallback for OpenF1 2026+ where session_type="Race" for both Race and Sprint
SESSION_NAME_MAP = {
    "race": "Race",
    "qualifying": "Qualifying",
    "sprint": "Sprint",
    "sprint qualifying": "Sprint Qualifying",
    "sprint shootout": "Sprint Qualifying",
    "sprint race": "Sprint",
}

SCORABLE_TYPES = set(SESSION_TYPE_MAP.values())


def _resolve_session_type(session_type: str, session_name: str) -> str | None:
    """
    Determine canonical session type using session_name as tiebreaker.
    OpenF1 2026+ uses session_type='Race' for both Race and Sprint,
    so session_name is needed to differentiate.
    """
    name_lower = (session_name or "").lower().strip()
    if name_lower in SESSION_NAME_MAP:
        return SESSION_NAME_MAP[name_lower]
    return SESSION_TYPE_MAP.get(session_type)


def get_scorable_sessions(year: int) -> list[SessionInfo]:
    raw_sessions = api.get_sessions(year)

    # Cross-reference meetings endpoint for reliable meeting_name
    raw_meetings = api.get_meetings(year)
    meeting_names: dict[int, str] = {
        m["meeting_key"]: m["meeting_name"]
        for m in raw_meetings
        if m.get("meeting_key") and m.get("meeting_name")
    }

    result = []
    for s in raw_sessions:
        canonical = _resolve_session_type(
            s.get("session_type", ""),
            s.get("session_name", ""),
        )
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
    sessions = get_scorable_sessions(year)
    grouped: dict[int, list[SessionInfo]] = {}
    for s in sessions:
        grouped.setdefault(s.meeting_key, []).append(s)
    return grouped


def fetch_grid_positions(qualifying_session_key: int) -> dict[int, int]:
    raw = api.get_session_result(qualifying_session_key)
    if raw:
        final: dict[int, int] = {}
        for entry in raw:
            num = entry.get("driver_number")
            pos = entry.get("position")
            if num is not None and pos is not None:
                final[num] = int(pos)
        if final:
            return final
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

    driver_map: dict[int, dict] = {
        d["driver_number"]: d for d in raw_drivers if d.get("driver_number") is not None
    }

    if is_quali_type:
        drivers, interval_data = _build_quali_drivers(sk, driver_map)
    else:
        # Prefer session_result for accurate final positions
        drivers = []
        raw_result = api.get_session_result(sk)
        if raw_result:
            for entry in raw_result:
                num = entry.get("driver_number")
                pos = entry.get("position")
                if num is None or pos is None:
                    continue
                d = driver_map.get(num, {})
                classified = (
                    not entry.get("dnf", False)
                    and not entry.get("dns", False)
                    and not entry.get("dsq", False)
                )
                drivers.append(DriverResult(
                    driver_number=num,
                    full_name=d.get("full_name", f"Driver #{num}"),
                    team_name=d.get("team_name", "Unknown"),
                    finish_position=int(pos),
                    grid_position=(grid_positions or {}).get(num, 0),
                    is_classified=classified,
                ))
        else:
            # Fallback: last position snapshot
            final_positions: dict[int, int] = {}
            for p in raw_position:
                num = p.get("driver_number")
                pos = p.get("position")
                if num is not None and pos is not None:
                    final_positions[num] = pos
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
    raw_result = api.get_session_result(session_key)

    if not raw_result:
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
            is_classified=not entry.get("dnf", False) and not entry.get("dns", False),
        ))

        # gap_to_leader in qualifying is an array [Q1_gap, Q2_gap, Q3_gap]
        gap_raw = entry.get("gap_to_leader")
        gap = None
        if isinstance(gap_raw, list):
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
            interval_data.append({"driver_number": num, "gap_to_leader": gap})

    drivers.sort(key=lambda x: x.finish_position)
    return drivers, interval_data
