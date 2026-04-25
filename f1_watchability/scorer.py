"""
Scoring engine — converts raw session data into a watchability score.

Pipeline:
  1. Calculate each factor score (0–100) using wider distribution curves
  2. Combine with weights into a base score
  3. Apply bonuses and penalties for exceptional/processional moments
  4. Clamp to 0–100 and look up recommendation from circuit-adjusted thresholds
"""

from __future__ import annotations

import math
import logging
from collections import defaultdict

from .config_loader import get_session_config
from .models import (
    BonusPenalty,
    ChampionshipEntry,
    DriverResult,
    FactorScore,
    RaceControlEvent,
    SessionRawData,
    SessionScore,
    WeatherSnapshot,
)

logger = logging.getLogger(__name__)

SESSION_TYPE_TO_CONFIG_KEY = {
    "Race": "race",
    "Qualifying": "qualifying",
    "Sprint": "sprint_race",
    "Sprint Qualifying": "sprint_qualifying",
}


# ── Curve helpers ─────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _linear(x: float, x_low: float, x_high: float, invert: bool = False) -> float:
    """
    Map x linearly onto 0–100 between x_low (→0) and x_high (→100).
    If invert=True, x_low maps to 100 and x_high maps to 0.
    Uses a wider curve than a simple clamp — values outside range still score 0 or 100.
    """
    if x_high == x_low:
        return 0.0
    score = 100.0 * (x - x_low) / (x_high - x_low)
    if invert:
        score = 100.0 - score
    return _clamp(score)


def _sigmoid(x: float, midpoint: float, steepness: float = 0.3) -> float:
    """Smooth S-curve mapping — avoids hard cliffs at the extremes."""
    return 100.0 / (1.0 + math.exp(-steepness * (x - midpoint)))


# ── Factor calculators ────────────────────────────────────────────────────────

def _score_close_finish(
    drivers: list[DriverResult],
    interval_data: list[dict],
) -> FactorScore:
    """Gap between P1 and P3 at the flag — smaller is better."""
    final_gaps: dict[int, float] = {}
    for entry in interval_data:
        num = entry.get("driver_number")
        gap = entry.get("gap_to_leader")
        if num is not None and gap is not None:
            try:
                final_gaps[num] = float(gap)
            except (TypeError, ValueError):
                pass

    top3 = sorted(drivers, key=lambda d: d.finish_position)[:3]
    gaps = [final_gaps[d.driver_number] for d in top3[1:] if d.driver_number in final_gaps]

    if not gaps:
        return FactorScore("close_finish", 50.0, 0.0, "Insufficient interval data")

    max_gap = max(gaps)
    # < 0.5s = perfect (100), 1s = great (85), 5s = okay (50), 30s+ = processional (0)
    score = _sigmoid(max_gap, midpoint=8.0, steepness=-0.3) * (100 / _sigmoid(0, 8.0, -0.3))
    score = _clamp(100.0 - _linear(max_gap, 0.0, 30.0))

    return FactorScore(
        name="close_finish",
        score=score,
        weight=0.0,  # weight set by caller
        reasoning=f"P1–P{1+len(gaps)} gap at the flag: {max_gap:.2f}s",
    )


def _score_overtakes(
    position_data: list[dict],
    pit_stops: list,
    drivers: list[DriverResult],
) -> FactorScore:
    """
    Count net position gains in the top 10, excluding pit-stop laps.
    Only counts on-track passes to avoid noise from pit cycles.
    """
    if not position_data:
        return FactorScore("overtakes", 50.0, 0.0, "No position data available")

    # Build per-driver lap→position timeline
    timeline: dict[int, dict[int, int]] = defaultdict(dict)
    for entry in position_data:
        num = entry.get("driver_number")
        lap = entry.get("lap_number") or 0
        pos = entry.get("position")
        if num is not None and pos is not None:
            timeline[num][lap] = pos

    # Pit laps to exclude (lap of stop + 1 buffer lap)
    pit_laps: dict[int, set[int]] = defaultdict(set)
    for p in pit_stops:
        pit_laps[p.driver_number].update({p.lap_number, p.lap_number + 1})

    # Top 10 driver numbers (by finish position)
    top10_nums = {d.driver_number for d in sorted(drivers, key=lambda x: x.finish_position)[:10]}

    overtake_count = 0
    for num, laps in timeline.items():
        if num not in top10_nums:
            continue
        sorted_laps = sorted(laps.items())
        for i in range(1, len(sorted_laps)):
            prev_lap, prev_pos = sorted_laps[i - 1]
            curr_lap, curr_pos = sorted_laps[i]
            if curr_pos < prev_pos and curr_lap not in pit_laps[num]:
                overtake_count += prev_pos - curr_pos

    # 0 = 0, 10 = 50, 30+ = 100 (wider curve than before)
    score = _clamp(_linear(overtake_count, 0, 35))
    return FactorScore(
        name="overtakes",
        score=score,
        weight=0.0,
        reasoning=f"Estimated {overtake_count} on-track position gains in top 10",
    )


def _score_safety_car(rc_events: list[RaceControlEvent]) -> FactorScore:
    """SC and VSC deployments."""
    sc = sum(
        1 for e in rc_events
        if ("safety car" in e.message.lower() or "safetycar" in e.category.lower())
        and "deploy" in e.message.lower()
    )
    vsc = sum(
        1 for e in rc_events
        if "virtual safety car" in e.message.lower() and "deploy" in e.message.lower()
    )
    total = sc + vsc
    score = _clamp(_linear(total, 0, 3))
    return FactorScore(
        name="safety_car",
        score=score,
        weight=0.0,
        reasoning=f"{sc} safety car deployment(s), {vsc} virtual safety car deployment(s)",
    )


def _score_pit_stop_variety(pit_stops: list, drivers: list[DriverResult]) -> FactorScore:
    """Strategic variety — range of stop counts and average stops per driver."""
    if not pit_stops:
        return FactorScore("pit_stop_variety", 20.0, 0.0, "No pit stop data")

    stops_per_driver: dict[int, int] = defaultdict(int)
    for p in pit_stops:
        stops_per_driver[p.driver_number] += 1

    counts = list(stops_per_driver.values())
    variety = max(counts) - min(counts)
    avg = sum(counts) / len(counts)

    score = _clamp(
        _linear(variety, 0, 3) * 0.55
        + _linear(avg, 1, 4) * 0.45
    )
    return FactorScore(
        name="pit_stop_variety",
        score=score,
        weight=0.0,
        reasoning=f"Average {avg:.1f} stops/driver, strategic spread of {variety} stop(s)",
    )


def _score_team_diversity(drivers: list[DriverResult]) -> FactorScore:
    """Number of different constructors in top 10."""
    top10 = sorted(drivers, key=lambda d: d.finish_position)[:10]
    teams = {d.team_name for d in top10}
    count = len(teams)
    # 4 teams = low, 7 = default, 10 = everyone
    score = _clamp(_linear(count, 3, 8))
    return FactorScore(
        name="team_diversity",
        score=score,
        weight=0.0,
        reasoning=f"{count} different constructor(s) in the top 10",
    )


def _score_grid_vs_result(drivers: list[DriverResult]) -> FactorScore:
    """Average position delta between grid and finish."""
    scorable = [d for d in drivers if d.grid_position > 0]
    if not scorable:
        return FactorScore("grid_vs_result", 50.0, 0.0, "Grid position data unavailable")

    avg_delta = sum(abs(d.finish_position - d.grid_position) for d in scorable) / len(scorable)
    # 0 places = 0, 3 places avg = 60, 6+ = 100
    score = _clamp(_linear(avg_delta, 0, 7))
    return FactorScore(
        name="grid_vs_result",
        score=score,
        weight=0.0,
        reasoning=f"Average grid-to-finish position change: {avg_delta:.1f} places",
    )


def _score_dnf_drama(
    drivers: list[DriverResult],
    championship_before: list[ChampionshipEntry],
    config: dict,
) -> FactorScore:
    """
    Notable retirements — a driver is 'notable' if any of:
      - Top 10 in championship standings at session start
      - Racing for a top 4 constructor (by championship position)
      - Started from a top 10 grid position
    """
    # Determine top 4 constructors dynamically from championship data
    # We use the team_name of drivers in the top 4 championship positions
    top_constructor_teams: set[str] = set()
    if championship_before:
        # Sort by position to find top 4 — use driver standings as proxy for constructor strength
        sorted_champ = sorted(championship_before, key=lambda e: e.position)
        for entry in sorted_champ[:8]:  # top 8 drivers likely represent top 4 teams
            top_constructor_teams.add(entry.team_name)
            if len(top_constructor_teams) >= 4:
                break

    top_champ_drivers = {
        e.driver_number for e in championship_before if e.position <= 10
    }

    # A driver retired if they started but their finish position is very high
    # (i.e. they dropped off the timing sheet or finished many laps down)
    classified_nums = {d.driver_number for d in drivers if d.is_classified}
    all_nums = {d.driver_number for d in drivers}
    # Drivers who started top 10 on grid
    top10_starters = {d.driver_number for d in drivers if 0 < d.grid_position <= 10}

    notable_retirements = []
    for d in drivers:
        if not d.is_classified:
            is_notable = (
                d.driver_number in top_champ_drivers
                or d.team_name in top_constructor_teams
                or d.driver_number in top10_starters
            )
            if is_notable:
                notable_retirements.append(d.full_name)

    count = len(notable_retirements)
    score = _clamp(_linear(count, 0, 3))

    if notable_retirements:
        reasoning = f"{count} notable retirement(s): {', '.join(notable_retirements[:3])}"
    else:
        reasoning = "No notable retirements"

    return FactorScore(name="dnf_drama", score=score, weight=0.0, reasoning=reasoning)


def _score_wet_weather(weather_samples: list[WeatherSnapshot]) -> FactorScore:
    """Rain and changing conditions."""
    if not weather_samples:
        return FactorScore("wet_weather", 0.0, 0.0, "No weather data")

    max_rainfall = max(w.rainfall for w in weather_samples)
    temp_range = max(w.track_temperature for w in weather_samples) - min(w.track_temperature for w in weather_samples)

    if max_rainfall < 0.1:
        score = 0.0
    else:
        # Rain up to 5mm = good, temp variance up to 20°C = good
        score = _clamp(
            _linear(max_rainfall, 0, 5) * 0.65
            + _linear(temp_range, 0, 20) * 0.35
        )

    return FactorScore(
        name="wet_weather",
        score=score,
        weight=0.0,
        reasoning=f"Max rainfall {max_rainfall:.1f}mm, track temp range {temp_range:.1f}°C",
    )


def _score_close_gaps(
    drivers: list[DriverResult],
    interval_data: list[dict],
) -> FactorScore:
    """For qualifying: gap between pole and P3."""
    final_gaps: dict[int, float] = {}
    for entry in interval_data:
        num = entry.get("driver_number")
        gap = entry.get("gap_to_leader")
        if num is not None and gap is not None:
            try:
                final_gaps[num] = float(gap)
            except (TypeError, ValueError):
                pass

    top3 = sorted(drivers, key=lambda d: d.finish_position)[:3]
    gaps = [final_gaps[d.driver_number] for d in top3[1:] if d.driver_number in final_gaps]

    if not gaps:
        return FactorScore("close_gaps", 50.0, 0.0, "Insufficient interval data")

    max_gap = max(gaps)
    # In quali: < 0.05s = incredible, 0.3s = okay, 1s+ = not competitive
    score = _clamp(_linear(max_gap, 0, 1.0, invert=True))
    return FactorScore(
        name="close_gaps",
        score=score,
        weight=0.0,
        reasoning=f"Pole to P{1+len(gaps)} gap: {max_gap:.3f}s",
    )


def _score_grid_shuffle(
    drivers: list[DriverResult],
    championship_before: list[ChampionshipEntry],
) -> FactorScore:
    """For qualifying: non-front-runner teams in top 5."""
    # Determine top 4 constructor teams from championship
    top_teams: set[str] = set()
    if championship_before:
        for entry in sorted(championship_before, key=lambda e: e.position)[:8]:
            top_teams.add(entry.team_name)
            if len(top_teams) >= 4:
                break

    top5 = sorted(drivers, key=lambda d: d.finish_position)[:5]
    surprises = sum(1 for d in top5 if d.team_name not in top_teams)
    score = _clamp(_linear(surprises, 0, 4))
    return FactorScore(
        name="grid_shuffle",
        score=score,
        weight=0.0,
        reasoning=f"{surprises} non-front-runner team(s) in the top 5 of qualifying",
    )


# ── Bonus / penalty detectors ─────────────────────────────────────────────────

def _detect_bonuses_penalties(
    data: SessionRawData,
    config: dict,
) -> list[BonusPenalty]:
    results: list[BonusPenalty] = []
    bp_cfg = config.get("bonuses", {}), config.get("penalties", {})
    bonuses, penalties = bp_cfg

    drivers = data.drivers
    rc = data.race_control
    position_data = data.position_data
    interval_data = data.interval_data
    session_type = data.session.session_type

    if session_type not in ("Race", "Sprint"):
        return results  # Bonuses/penalties only apply to race-type sessions

    total_laps = max((p.get("lap_number", 0) for p in position_data), default=0)

    # ── Last lap lead change ──────────────────────────────────────────────────
    if total_laps > 0:
        last_lap_leaders: set[int] = set()
        second_last_lap_leaders: set[int] = set()
        for p in position_data:
            lap = p.get("lap_number", 0)
            pos = p.get("position")
            num = p.get("driver_number")
            if pos == 1 and num is not None:
                if lap == total_laps:
                    last_lap_leaders.add(num)
                elif lap == total_laps - 1:
                    second_last_lap_leaders.add(num)

        if last_lap_leaders and second_last_lap_leaders and not last_lap_leaders.intersection(second_last_lap_leaders):
            results.append(BonusPenalty(
                name="last_lap_lead_change",
                points=bonuses.get("last_lap_lead_change", 15),
                reasoning="Lead changed on the final lap",
            ))

    # ── Sub-1-second finish ───────────────────────────────────────────────────
    final_gaps: dict[int, float] = {}
    for entry in interval_data:
        num = entry.get("driver_number")
        gap = entry.get("gap_to_leader")
        if num is not None and gap is not None:
            try:
                final_gaps[num] = float(gap)
            except (TypeError, ValueError):
                pass

    top2 = sorted(drivers, key=lambda d: d.finish_position)[:2]
    if len(top2) == 2:
        p2_gap = final_gaps.get(top2[1].driver_number)
        if p2_gap is not None and p2_gap < 1.0:
            results.append(BonusPenalty(
                name="sub_one_second_finish",
                points=bonuses.get("sub_one_second_finish", 10),
                reasoning=f"P1–P2 gap at the flag was {p2_gap:.3f}s",
            ))

    # ── Red flag restart ──────────────────────────────────────────────────────
    red_flags = [e for e in rc if "red flag" in e.message.lower() and "withdraw" not in e.message.lower()]
    restarts = [e for e in rc if "session resumed" in e.message.lower() or "restart" in e.message.lower()]
    if red_flags and restarts:
        results.append(BonusPenalty(
            name="red_flag_restart",
            points=bonuses.get("red_flag_restart", 8),
            reasoning=f"{len(red_flags)} red flag(s) with restart(s)",
        ))

    # ── Championship lead change ──────────────────────────────────────────────
    if data.championship_before and data.championship_after:
        leader_before = min(data.championship_before, key=lambda e: e.position, default=None)
        leader_after = min(data.championship_after, key=lambda e: e.position, default=None)
        if (leader_before and leader_after
                and leader_before.driver_number != leader_after.driver_number):
            results.append(BonusPenalty(
                name="championship_lead_change",
                points=bonuses.get("championship_lead_change", 10),
                reasoning="The championship lead changed hands as a result of this race",
            ))

    # ── Multiple SC deployments ───────────────────────────────────────────────
    sc_count = sum(
        1 for e in rc
        if ("safety car" in e.message.lower() and "deploy" in e.message.lower())
        or ("virtual safety car" in e.message.lower() and "deploy" in e.message.lower())
    )
    if sc_count > 1:
        results.append(BonusPenalty(
            name="multiple_sc",
            points=bonuses.get("multiple_sc", 6),
            reasoning=f"{sc_count} safety car/VSC deployments",
        ))

    # ── Dominant leader penalty ───────────────────────────────────────────────
    if total_laps >= 10:
        # Find gap to leader at ~lap 10
        lap10_gaps: list[float] = []
        for entry in interval_data:
            lap = entry.get("lap_number", 0)
            gap = entry.get("gap_to_leader")
            if 9 <= lap <= 12 and gap is not None:
                try:
                    lap10_gaps.append(float(gap))
                except (TypeError, ValueError):
                    pass

        # Check if any driver had 20+ second gap at lap 10
        # and the leader held on (check they won)
        if lap10_gaps and max(lap10_gaps) >= 20:
            winner = min(drivers, key=lambda d: d.finish_position)
            # Check winner led from early on (grid P1 or P2)
            if winner.grid_position in (1, 2, 0):
                results.append(BonusPenalty(
                    name="dominant_leader",
                    points=penalties.get("dominant_leader", -12),
                    reasoning="Leader built a 20+ second gap by lap 10 and dominated throughout",
                ))

    # ── No top-10 position changes penalty ────────────────────────────────────
    top10_nums = {d.driver_number for d in sorted(drivers, key=lambda x: x.finish_position)[:10]}
    pit_laps: dict[int, set[int]] = defaultdict(set)
    for p in data.pit_stops:
        pit_laps[p.driver_number].update({p.lap_number, p.lap_number + 1})

    top10_changes = 0
    timeline: dict[int, dict[int, int]] = defaultdict(dict)
    for entry in position_data:
        num = entry.get("driver_number")
        lap = entry.get("lap_number") or 0
        pos = entry.get("position")
        if num is not None and pos is not None:
            timeline[num][lap] = pos

    for num in top10_nums:
        laps = sorted(timeline.get(num, {}).items())
        for i in range(1, len(laps)):
            prev_lap, prev_pos = laps[i - 1]
            curr_lap, curr_pos = laps[i]
            if curr_pos < prev_pos and curr_lap not in pit_laps[num]:
                top10_changes += 1

    if top10_changes == 0 and total_laps > 5:
        results.append(BonusPenalty(
            name="no_top10_position_changes",
            points=penalties.get("no_top10_position_changes", -10),
            reasoning="No on-track position changes in the top 10 throughout the race",
        ))

    # ── Result mirrors grid penalty ───────────────────────────────────────────
    top5_by_finish = sorted(drivers, key=lambda d: d.finish_position)[:5]
    top5_grid_positions = [d.grid_position for d in top5_by_finish if d.grid_position > 0]
    if len(top5_grid_positions) == 5 and top5_grid_positions == [1, 2, 3, 4, 5]:
        results.append(BonusPenalty(
            name="result_mirrors_grid",
            points=penalties.get("result_mirrors_grid", -8),
            reasoning="Top 5 finishing order was identical to the starting grid",
        ))

    return results


# ── Main scoring entry point ──────────────────────────────────────────────────

def score_session(data: SessionRawData, config: dict) -> SessionScore:
    session_type = data.session.session_type
    circuit = data.session.circuit_short_name
    cfg_key = SESSION_TYPE_TO_CONFIG_KEY.get(session_type, "race")

    session_cfg, override_applied = get_session_config(config, cfg_key, circuit)
    weights: dict[str, float] = session_cfg.get("weights", {})
    thresholds: dict[str, float] = session_cfg.get("thresholds", {})

    # ── Calculate factors ─────────────────────────────────────────────────────
    drivers = data.drivers
    is_race_type = session_type in ("Race", "Sprint")
    is_quali_type = session_type in ("Qualifying", "Sprint Qualifying")

    raw_factors: dict[str, FactorScore] = {}

    if is_race_type:
        raw_factors["close_finish"] = _score_close_finish(drivers, data.interval_data)
        raw_factors["overtakes"] = _score_overtakes(data.position_data, data.pit_stops, drivers)
        raw_factors["safety_car"] = _score_safety_car(data.race_control)
        raw_factors["pit_stop_variety"] = _score_pit_stop_variety(data.pit_stops, drivers)
        raw_factors["team_diversity"] = _score_team_diversity(drivers)
        raw_factors["grid_vs_result"] = _score_grid_vs_result(drivers)
        raw_factors["dnf_drama"] = _score_dnf_drama(drivers, data.championship_before, config)
        raw_factors["wet_weather"] = _score_wet_weather(data.weather_samples)

    elif is_quali_type:
        raw_factors["close_gaps"] = _score_close_gaps(drivers, data.interval_data)
        raw_factors["grid_shuffle"] = _score_grid_shuffle(drivers, data.championship_before)
        raw_factors["dnf_drama"] = _score_dnf_drama(drivers, data.championship_before, config)
        raw_factors["wet_weather"] = _score_wet_weather(data.weather_samples)

    # ── Apply weights ─────────────────────────────────────────────────────────
    factor_scores: list[FactorScore] = []
    weighted_sum = 0.0
    total_weight = 0.0

    for name, weight in weights.items():
        if name not in raw_factors:
            continue
        fs = raw_factors[name]
        fs.weight = weight
        factor_scores.append(fs)
        weighted_sum += fs.score * weight
        total_weight += weight

    base_score = (weighted_sum / total_weight) if total_weight > 0 else 50.0

    # ── Apply bonuses/penalties ───────────────────────────────────────────────
    bonuses_penalties = _detect_bonuses_penalties(data, config)
    adjustment = sum(bp.points for bp in bonuses_penalties)
    total_score = _clamp(base_score + adjustment)

    # ── Recommendation ────────────────────────────────────────────────────────
    watch_full = thresholds.get("watch_full", 65)
    race_in_30 = thresholds.get("race_in_30")  # None for qualifying

    if total_score >= watch_full:
        recommendation = "Watch Full"
    elif race_in_30 is not None and total_score >= race_in_30:
        recommendation = "Race in 30"
    else:
        recommendation = "Watch Highlights"

    return SessionScore(
        session=data.session,
        factors=factor_scores,
        bonuses_penalties=bonuses_penalties,
        base_score=round(base_score, 1),
        total_score=round(total_score, 1),
        recommendation=recommendation,
        circuit_override_applied=override_applied,
    )
