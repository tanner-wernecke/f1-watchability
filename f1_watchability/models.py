"""
Dataclasses used throughout the application.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionInfo:
    session_key: int
    session_name: str
    session_type: str       # "Race" | "Qualifying" | "Sprint" | "Sprint Qualifying"
    meeting_name: str
    circuit_short_name: str
    date_start: str
    year: int
    meeting_key: int


@dataclass
class DriverResult:
    driver_number: int
    full_name: str
    team_name: str
    finish_position: int
    grid_position: int      # 0 = unknown
    is_classified: bool = True


@dataclass
class RaceControlEvent:
    category: str
    message: str
    lap_number: Optional[int] = None


@dataclass
class PitStop:
    driver_number: int
    lap_number: int
    pit_duration: Optional[float] = None


@dataclass
class WeatherSnapshot:
    rainfall: float
    track_temperature: float
    air_temperature: float
    wind_speed: float


@dataclass
class ChampionshipEntry:
    driver_number: int
    full_name: str
    team_name: str
    points: float
    position: int           # championship standing at time of session


@dataclass
class SessionRawData:
    session: SessionInfo
    drivers: list[DriverResult] = field(default_factory=list)
    pit_stops: list[PitStop] = field(default_factory=list)
    race_control: list[RaceControlEvent] = field(default_factory=list)
    weather_samples: list[WeatherSnapshot] = field(default_factory=list)
    position_data: list[dict] = field(default_factory=list)
    lap_data: list[dict] = field(default_factory=list)
    interval_data: list[dict] = field(default_factory=list)
    # Championship standings at the START of this session (before points awarded)
    championship_before: list[ChampionshipEntry] = field(default_factory=list)
    # Championship standings at the END of this session
    championship_after: list[ChampionshipEntry] = field(default_factory=list)


@dataclass
class FactorScore:
    name: str
    score: float            # 0–100
    weight: float
    reasoning: str          # Spoiler-containing detail — hidden by default


@dataclass
class BonusPenalty:
    name: str
    points: float           # Positive = bonus, negative = penalty
    reasoning: str


@dataclass
class SessionScore:
    session: SessionInfo
    factors: list[FactorScore]
    bonuses_penalties: list[BonusPenalty]
    base_score: float       # Weighted factor score before bonuses/penalties
    total_score: float      # Final clamped 0–100 score
    recommendation: str     # "Watch Full" | "Race in 30" | "Watch Highlights"
    circuit_override_applied: bool = False


@dataclass
class WeekendReport:
    meeting_name: str
    circuit_short_name: str
    year: int
    sessions: list[SessionScore]
