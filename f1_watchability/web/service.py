"""
Shared scoring service — used by both the CLI and the web app.
Encapsulates the full pipeline: fetch calendar → fetch session data → score.
Results are cached to disk keyed on (year, meeting_key, config_hash).
"""

from __future__ import annotations

import logging

from ..config_loader import load_config
from ..fetcher import fetch_grid_positions, fetch_session_data, get_meetings_with_sessions
from ..models import SessionInfo, SessionScore, WeekendReport, FactorScore, BonusPenalty
from ..scorer import score_session
from ..season_stats import build_season_stats
from . import cache as cache_store

logger = logging.getLogger(__name__)


def get_calendar(year: int) -> list[dict]:
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


def score_weekend(meeting_key: int, year: int, config: dict | None = None) -> "WeekendReport | _CachedReport | None":
    if config is None:
        config = load_config()

    cfg_hash = cache_store.config_hash(config)

    # ── Cache check ───────────────────────────────────────────────────────────
    cached = cache_store.get(year, meeting_key, cfg_hash)
    if cached is not None:
        logger.info(f"Serving from cache: {year} meeting {meeting_key}")
        return _CachedReport(cached)

    # ── Fetch sessions ────────────────────────────────────────────────────────
    meetings = get_meetings_with_sessions(year=year)
    sessions: list[SessionInfo] = meetings.get(meeting_key, [])
    if not sessions:
        return None

    # ── Grid position enrichment ──────────────────────────────────────────────
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

    # ── Season stats for relative normalisation ───────────────────────────────
    season_stats = _build_season_stats_for(year, meeting_key, config, cfg_hash)

    # ── Score each session ────────────────────────────────────────────────────
    scored: list[SessionScore] = []
    for session_info in sessions:
        try:
            grid_positions = grid_position_cache.get(session_info.session_type)
            raw = fetch_session_data(session_info, grid_positions=grid_positions)
            if not raw.drivers:
                logger.warning(
                    f"No drivers found for {session_info.session_name} "
                    f"(key={session_info.session_key}) — skipping"
                )
                continue
            scored.append(score_session(raw, config, season_stats=season_stats))
        except Exception as e:
            logger.exception(f"Could not score {session_info.session_name}: {e}")

    if not scored:
        return None

    sample = sessions[0]
    report = WeekendReport(
        meeting_name=sample.meeting_name,
        circuit_short_name=sample.circuit_short_name,
        year=year,
        sessions=scored,
    )

    report_dict = weekend_report_to_dict(report)
    cache_store.set(year, meeting_key, cfg_hash, report_dict)
    return report


def _build_season_stats_for(year: int, current_meeting_key: int, config: dict, cfg_hash: str):
    from ..models import SessionInfo as SI

    completed_reports = []
    all_meetings = get_meetings_with_sessions(year=year)

    for mk in all_meetings:
        if mk == current_meeting_key:
            continue
        cached = cache_store.get(year, mk, cfg_hash)
        if cached is None:
            continue
        completed_reports.append(_CachedReport(cached))

    return build_season_stats(completed_reports, current_meeting_key)


def weekend_report_to_dict(report) -> dict:
    """Serialise a WeekendReport (or _CachedReport) to a JSON-safe dict."""
    if isinstance(report, _CachedReport):
        return report.to_dict()
    return {
        "meeting_name":       report.meeting_name,
        "circuit_short_name": report.circuit_short_name,
        "year":               report.year,
        "sessions": [
            {
                "session_type":     ss.session.session_type,
                "session_name":     ss.session.session_name,
                "date_start":       ss.session.date_start,
                "total_score":      ss.total_score,
                "base_score":       ss.base_score,
                "recommendation":   ss.recommendation,
                "circuit_override": ss.circuit_override_applied,
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
    order = ["Sprint Qualifying", "Sprint", "Qualifying", "Race"]
    by_type = {ss.session.session_type: ss for ss in sessions}
    return [by_type[t] for t in order if t in by_type]


class _CachedReport:
    """Wraps a cached dict so it can be used like a WeekendReport."""
    def __init__(self, data: dict):
        self._data = data
        self.meeting_name       = data["meeting_name"]
        self.circuit_short_name = data["circuit_short_name"]
        self.year               = data["year"]
        self.sessions           = _sessions_from_dict(data)

    def to_dict(self) -> dict:
        return self._data


def _sessions_from_dict(data: dict) -> list:
    """Reconstruct minimal SessionScore-like objects from a cached dict."""
    from ..models import SessionScore, SessionInfo

    results = []
    for s in data.get("sessions", []):
        session_info = SessionInfo(
            session_key=0,
            session_name=s.get("session_name", ""),
            session_type=s.get("session_type", ""),
            meeting_name=data["meeting_name"],
            circuit_short_name=data["circuit_short_name"],
            date_start=s.get("date_start", ""),
            year=data["year"],
            meeting_key=0,
        )
        factors = [
            FactorScore(name=f["name"], score=f["score"], weight=f["weight"], reasoning=f["reasoning"])
            for f in s.get("factors", [])
        ]
        bps = [
            BonusPenalty(name=b["name"], points=b["points"], reasoning=b["reasoning"])
            for b in s.get("bonuses_penalties", [])
        ]
        results.append(SessionScore(
            session=session_info,
            factors=factors,
            bonuses_penalties=bps,
            base_score=s.get("base_score", 50.0),
            total_score=s.get("total_score", 50.0),
            recommendation=s.get("recommendation", "Watch Highlights"),
            circuit_override_applied=s.get("circuit_override", False),
        ))
    return results
