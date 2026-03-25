#!/usr/bin/env python3
"""
F1 Watchability CLI

  f1watch                              Full season overview + latest race
  f1watch --race "Monaco"              Single race weekend
  f1watch --race "Monaco" --spoilers   Show full reasoning (watch first!)
  f1watch --list                       List available race weekends
  f1watch --year 2024                  Different season
  f1watch --config my_weights.yaml     Custom weights
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config_loader import load_config
from .display import console, make_progress, render_season_table, render_weekend_report
from .fetcher import fetch_grid_positions, fetch_session_data, get_meetings_with_sessions
from .models import SessionInfo, WeekendReport
from .scorer import score_session

from rich.console import Console
err = Console(stderr=True)


def _build_report(
    sessions: list[SessionInfo],
    config: dict,
    progress=None,
) -> WeekendReport | None:
    if not sessions:
        return None

    # Pre-fetch grid positions from qualifying sessions so races can use them.
    # Maps: "Race" → quali session key, "Sprint" → sprint qualifying session key
    grid_position_cache: dict[str, dict[int, int]] = {}

    quali_for_race   = next((s for s in sessions if s.session_type == "Qualifying"), None)
    quali_for_sprint = next((s for s in sessions if s.session_type == "Sprint Qualifying"), None)

    if quali_for_race:
        desc = f"  Fetching grid positions — {quali_for_race.meeting_name}..."
        task = progress.add_task(desc, total=None) if progress else None
        try:
            grid_position_cache["Race"] = fetch_grid_positions(quali_for_race.session_key)
        except Exception as e:
            logging.warning(f"Could not fetch qualifying grid positions: {e}")
        finally:
            if progress and task is not None:
                progress.remove_task(task)

    if quali_for_sprint:
        desc = f"  Fetching sprint grid positions — {quali_for_sprint.meeting_name}..."
        task = progress.add_task(desc, total=None) if progress else None
        try:
            grid_position_cache["Sprint"] = fetch_grid_positions(quali_for_sprint.session_key)
        except Exception as e:
            logging.warning(f"Could not fetch sprint qualifying grid positions: {e}")
        finally:
            if progress and task is not None:
                progress.remove_task(task)

    scored = []
    for session_info in sessions:
        desc = f"  Scoring {session_info.session_name} — {session_info.meeting_name}..."
        task = progress.add_task(desc, total=None) if progress else None
        try:
            grid_positions = grid_position_cache.get(session_info.session_type)
            raw = fetch_session_data(session_info, grid_positions=grid_positions)
            scored.append(score_session(raw, config))
        except Exception as e:
            logging.warning(f"Could not score {session_info.session_name}: {e}")
        finally:
            if progress and task is not None:
                progress.remove_task(task)

    if not scored:
        return None

    sample = sessions[0]
    return WeekendReport(
        meeting_name=sample.meeting_name,
        circuit_short_name=sample.circuit_short_name,
        year=sample.year,
        sessions=scored,
    )


def cmd_list(year: int) -> None:
    with make_progress() as p:
        task = p.add_task(f"Fetching {year} calendar...", total=None)
        meetings = get_meetings_with_sessions(year)
        p.remove_task(task)

    console.print(f"\n[bold cyan]📅 {year} F1 Season[/bold cyan]\n")
    for i, (_, sessions) in enumerate(meetings.items(), 1):
        s = sessions[0]
        types = {sess.session_type for sess in sessions}
        sprint = "[yellow] ⚡ Sprint Weekend[/yellow]" if "Sprint" in types else ""
        console.print(f"  {i:>2}. [white]{s.meeting_name}[/white]  [dim]{s.circuit_short_name}[/dim]{sprint}")
    console.print()


def cmd_race(year: int, race_filter: str, config: dict, spoilers: bool) -> None:
    with make_progress() as p:
        task = p.add_task(f"Fetching {year} calendar...", total=None)
        meetings = get_meetings_with_sessions(year)
        p.remove_task(task)

    match = next(
        ((k, v) for k, v in meetings.items()
         if v and race_filter.lower() in v[0].meeting_name.lower()),
        None,
    )
    if match is None:
        err.print(f"[red]No race found matching '{race_filter}'. Use --list to see options.[/red]")
        sys.exit(1)

    _, sessions = match
    with make_progress() as p:
        report = _build_report(sessions, config, p)

    if report:
        render_weekend_report(report, spoilers=spoilers)


def cmd_season(year: int, config: dict) -> None:
    with make_progress() as p:
        task = p.add_task(f"Fetching {year} calendar...", total=None)
        meetings = get_meetings_with_sessions(year)
        p.remove_task(task)

    if not meetings:
        err.print(f"[red]No meetings found for {year}.[/red]")
        sys.exit(1)

    reports: list[WeekendReport] = []
    with make_progress() as p:
        for _, sessions in meetings.items():
            report = _build_report(sessions, config, p)
            if report:
                reports.append(report)

    if not reports:
        err.print("[red]No completed sessions found.[/red]")
        sys.exit(1)

    render_weekend_report(reports[-1])
    if len(reports) > 1:
        render_season_table(reports)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="F1 Watchability — find out which sessions are worth your time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--year",     type=int, default=2024, help="Season year (default: 2024)")
    parser.add_argument("--race",     type=str, default=None, help="Race name to score (partial match)")
    parser.add_argument("--list",     action="store_true",    help="List available race weekends")
    parser.add_argument("--spoilers", action="store_true",    help="Show full factor reasoning (watch first!)")
    parser.add_argument("--config",   type=str, default=None, help="Path to custom weights YAML")
    parser.add_argument("--debug",    action="store_true",    help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    config = load_config(args.config)

    if args.list:
        cmd_list(args.year)
    elif args.race:
        cmd_race(args.year, args.race, config, spoilers=args.spoilers)
    else:
        cmd_season(args.year, config)


if __name__ == "__main__":
    main()
