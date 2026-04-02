"""
Season statistics — computes per-factor averages across completed race sessions
in a given season. Used to normalise scores relative to the season baseline
rather than all-time absolute values.

Falls back to absolute scoring when fewer than MIN_RACES_FOR_RELATIVE completed
sessions are available (early season).
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MIN_RACES_FOR_RELATIVE = 4  # Min completed races before relative scoring kicks in


@dataclass
class FactorStats:
    """Running statistics for a single factor across the season."""
    values: list[float] = field(default_factory=list)

    def add(self, value: float) -> None:
        self.values.append(value)

    @property
    def mean(self) -> float:
        return statistics.mean(self.values) if self.values else 50.0

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.values) if len(self.values) >= 2 else 20.0

    def normalise(self, value: float) -> float:
        """
        Map value onto 0–100 relative to the season distribution.
        Uses z-score: mean → 50, ±2 stdev → ~5/~95.
        """
        if len(self.values) < 2:
            return value
        sd = self.stdev
        if sd == 0:
            return 50.0
        z = (value - self.mean) / sd
        normalised = 50.0 + z * 22.5
        return max(0.0, min(100.0, normalised))


@dataclass
class SeasonStats:
    """Aggregated factor statistics across all completed sessions in a season."""
    year: int
    race_count: int = 0
    factors: dict[str, FactorStats] = field(default_factory=dict)

    def has_enough_data(self) -> bool:
        return self.race_count >= MIN_RACES_FOR_RELATIVE

    def record_factor(self, name: str, value: float) -> None:
        if name not in self.factors:
            self.factors[name] = FactorStats()
        self.factors[name].add(value)

    def normalise_factor(self, name: str, raw_score: float) -> float:
        if not self.has_enough_data():
            return raw_score
        if name not in self.factors or len(self.factors[name].values) < 2:
            return raw_score
        return self.factors[name].normalise(raw_score)


def build_season_stats(completed_reports: list, current_meeting_key: int) -> SeasonStats:
    """
    Build a SeasonStats from all completed weekend reports EXCLUDING
    the current meeting so we don't bias the current race's own score.
    """
    year = completed_reports[0].year if completed_reports else 0
    stats = SeasonStats(year=year)

    for report in completed_reports:
        for ss in report.sessions:
            if ss.session.session_type not in ("Race", "Sprint"):
                continue
            if ss.session.meeting_key == current_meeting_key:
                continue
            stats.race_count += 1
            for f in ss.factors:
                # Strip season-adjustment suffix if present (from previous normalisation)
                base_score = f.score
                stats.record_factor(f.name, base_score)

    return stats
