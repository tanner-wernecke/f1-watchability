"""
Shared scoring service — used by both the CLI and the web app.
Encapsulates the full pipeline: fetch calendar → fetch session data → score.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from ..config_loader import load_config
from ..fetcher import fetch_grid_positions, fetch_session_data, get_meetings_with_sessions
from ..models import SessionInfo, SessionScore, WeekendReport
from ..scorer import score_session

logger = logging.getLogger(__name__)


def get_calendar(year: int) -> list[dict]:
    """
    Returns a list of race weekends for a given year, each as a dict with
    meeting_key, meeting_name, circuit_short_name, is_sprint_weekend.
    """
    meetings = get_meetings_with_sessions(year=year)
    result = []
    for meeting_key, sessions in meetings.items():
        if not sessions:
            continue
        sample = sessions[0]
        types = {s.session_type for s in sessions}
        result.append({
            "meeting_key":        meeting_key,
            "meeting_name":       sample.meeting_name,
            "circuit_short_name": sample.circuit_short_name,
            "date_start":         sample.date_start,
            "is_sprint_weekend":  "Sprint" in types,
        })
    return result


def score_weekend(meeting_key: int, year: int, config: dict | None = None) -> WeekendReport | None:
    """
    Fetches and scores all sessions for a given meeting.
    Returns a WeekendReport or None if no sessions could be scored.
    """
    if config is None:
        config = load_config()

    meetings = get_meetings_with_sessions(year=year)
    sessions: list[SessionInfo] = meetings.get(meeting_key, [])
    if not sessions:
        return None

    # Grid position enrichment
    grid_position_cache: dict[str, dict[int, int]] = {}
    quali_for_race   = next((s for s in sessions if s.session_type == "Qualifying"), None)
    quali_for_sprint = next((s for s in sessions if s.session_type == "Sprint Qualifying"), None)

    if quali_for_race:
        try:
            grid_position_cache["Race"] = fetch_grid_positions(quali_for_race.session_key)
        except Exception as e:
            logger.warning(f"Could not fetch qualifying grid positions: {e}")

    if quali_for_sprint:
        try:
            grid_position_cache["Sprint"] = fetch_grid_positions(quali_for_sprint.session_key)
        except Exception as e:
            logger.warning(f"Could not fetch sprint qualifying grid positions: {e}")

    scored: list[SessionScore] = []
    for session_info in sessions:
        try:
            grid_positions = grid_position_cache.get(session_info.session_type)
            raw = fetch_session_data(session_info, grid_positions=grid_positions)
            scored.append(score_session(raw, config))
        except Exception as e:
            logger.warning(f"Could not score {session_info.session_name}: {e}")

    if not scored:
        return None

    sample = sessions[0]
    return WeekendReport(
        meeting_name=sample.meeting_name,
        circuit_short_name=sample.circuit_short_name,
        year=year,
        sessions=scored,
    )


def weekend_report_to_dict(report: WeekendReport) -> dict:
    """Serialise a WeekendReport to a JSON-safe dict for the web API."""
    return {
        "meeting_name":       report.meeting_name,
        "circuit_short_name": report.circuit_short_name,
        "year":               report.year,
        "sessions": [
            {
                "session_type":       ss.session.session_type,
                "session_name":       ss.session.session_name,
                "date_start":         ss.session.date_start,
                "total_score":        ss.total_score,
                "base_score":         ss.base_score,
                "recommendation":     ss.recommendation,
                "circuit_override":   ss.circuit_override_applied,
                "factors": [
                    {
                        "name":      f.name,
                        "score":     f.score,
                        "weight":    f.weight,
                        "reasoning": f.reasoning,
                    }
                    for f in sorted(ss.factors, key=lambda x: x.score * x.weight, reverse=True)
                ],
                "bonuses_penalties": [
                    {
                        "name":      bp.name,
                        "points":    bp.points,
                        "reasoning": bp.reasoning,
                    }
                    for bp in ss.bonuses_penalties
                ],
            }
            for ss in ss_order(report.sessions)
        ],
    }


def ss_order(sessions: list[SessionScore]) -> list[SessionScore]:
    """Return sessions in display order: SQ → Sprint → Qualifying → Race."""
    order = ["Sprint Qualifying", "Sprint", "Qualifying", "Race"]
    by_type = {ss.session.session_type: ss for ss in sessions}
    return [by_type[t] for t in order if t in by_type]
