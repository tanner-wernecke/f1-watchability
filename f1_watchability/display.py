"""
CLI display — renders watchability reports using Rich.
Spoiler-safe by default. Pass spoilers=True to reveal factor reasoning.
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn

from .models import BonusPenalty, FactorScore, SessionScore, WeekendReport

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score_colour(score: float) -> str:
    if score >= 70:   return "bright_green"
    if score >= 55:   return "green"
    if score >= 40:   return "yellow"
    if score >= 28:   return "orange1"
    return "red"


def _rec_style(rec: str) -> str:
    return {
        "Watch Full":       "bold bright_green",
        "Race in 30":       "bold yellow",
        "Watch Highlights": "bold orange1",
    }.get(rec, "white")


def _bar(score: float, width: int = 20) -> str:
    filled = int(round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


SESSION_EMOJI = {
    "Race":              "🏁",
    "Sprint":            "⚡",
    "Qualifying":        "⏱️ ",
    "Sprint Qualifying": "🎯",
}

SESSION_LABEL = {
    "Race":              "MAIN RACE",
    "Sprint":            "SPRINT RACE",
    "Qualifying":        "QUALIFYING",
    "Sprint Qualifying": "SPRINT QUALIFYING",
}

REC_EMOJI = {
    "Watch Full":       "✅",
    "Race in 30":       "🟡",
    "Watch Highlights": "📋",
}


# ── Session card ──────────────────────────────────────────────────────────────

def render_session_card(ss: SessionScore, spoilers: bool = False) -> Panel:
    colour = _score_colour(ss.total_score)
    emoji  = SESSION_EMOJI.get(ss.session.session_type, "🏎️")
    label  = SESSION_LABEL.get(ss.session.session_type, ss.session.session_type)
    rec_e  = REC_EMOJI.get(ss.recommendation, "")

    from rich.console import Group

    # Header
    header = Text()
    header.append(f"{emoji}  {label}\n", style="bold white")
    header.append(f"  {_bar(ss.total_score)}  ", style=colour)
    header.append(f"{ss.total_score:.0f}/100", style=f"bold {colour}")
    if ss.circuit_override_applied:
        header.append("  [dim](circuit-adjusted)[/dim]")
    header.append(f"\n\n  {rec_e}  ", style="")
    header.append(ss.recommendation, style=_rec_style(ss.recommendation))

    elements = [header]

    # Bonuses / penalties (always shown — they don't spoil)
    visible_bps = [bp for bp in ss.bonuses_penalties if not spoilers]
    if ss.bonuses_penalties:
        bp_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        bp_table.add_column(width=3)
        bp_table.add_column()
        bp_table.add_column(justify="right", width=6)
        for bp in ss.bonuses_penalties:
            sign = "+" if bp.points > 0 else ""
            colour_bp = "green" if bp.points > 0 else "red"
            label_str = bp.name.replace("_", " ").title()
            if spoilers:
                label_str = f"{label_str} — [dim]{bp.reasoning}[/dim]"
            bp_table.add_row(
                Text("▲" if bp.points > 0 else "▼", style=colour_bp),
                label_str,
                Text(f"{sign}{bp.points:.0f}", style=f"bold {colour_bp}"),
            )
        elements.append(bp_table)

    # Factor table (reasoning hidden unless spoilers=True)
    if spoilers and ss.factors:
        fac_table = Table(box=box.SIMPLE, show_header=True, header_style="dim", padding=(0, 1))
        fac_table.add_column("Factor", style="dim white", no_wrap=True)
        fac_table.add_column("Score", justify="right", width=6)
        fac_table.add_column("Bar", width=14)
        fac_table.add_column("Detail", style="dim")

        for f in sorted(ss.factors, key=lambda x: x.score * x.weight, reverse=True):
            fc = _score_colour(f.score)
            fac_table.add_row(
                f.name.replace("_", " ").title(),
                Text(f"{f.score:.0f}", style=f"bold {fc}"),
                Text(_bar(f.score, 14), style=fc),
                f.reasoning,
            )
        elements.append(fac_table)

    return Panel(Group(*elements), border_style=colour, padding=(0, 1))


# ── Weekend report ────────────────────────────────────────────────────────────

def render_weekend_report(report: WeekendReport, spoilers: bool = False) -> None:
    console.print()
    console.print(Rule(
        f"[bold white]🏎️  {report.meeting_name} {report.year}[/bold white]",
        style="bold cyan",
    ))
    console.print(f"  [dim]📍 {report.circuit_short_name}[/dim]\n")

    if not spoilers:
        console.print(
            "  [dim]Scores shown without spoilers. "
            "Run with [bold]--spoilers[/bold] after watching to see full reasoning.[/dim]\n"
        )

    order = ["Sprint Qualifying", "Sprint", "Qualifying", "Race"]
    by_type = {s.session.session_type: s for s in report.sessions}

    for stype in order:
        if stype in by_type:
            console.print(render_session_card(by_type[stype], spoilers=spoilers))

    console.print()


# ── Season overview ───────────────────────────────────────────────────────────

def render_season_table(reports: list[WeekendReport]) -> None:
    console.print()
    console.print(Rule("[bold white]📅 Season Overview[/bold white]", style="cyan"))
    console.print()

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
    table.add_column("Race Weekend", min_width=24)
    table.add_column("Quali",  justify="center", width=8)
    table.add_column("Race",   justify="center", width=8)
    table.add_column("SQ",     justify="center", width=6)
    table.add_column("Sprint", justify="center", width=8)
    table.add_column("Recommendations", min_width=40)

    rec_short = {
        "Watch Full":       "✅ Full",
        "Race in 30":       "🟡 30min",
        "Watch Highlights": "📋 Highlights",
    }

    for report in reports:
        by_type = {s.session.session_type: s for s in report.sessions}

        def cell(stype: str) -> Text:
            if stype not in by_type:
                return Text("—", style="dim")
            s = by_type[stype]
            c = _score_colour(s.total_score)
            return Text(f"{s.total_score:.0f}", style=f"bold {c}")

        recs = []
        for stype, short_label in [("Qualifying","Q"), ("Race","R"), ("Sprint Qualifying","SQ"), ("Sprint","S")]:
            if stype in by_type:
                s = by_type[stype]
                recs.append(f"[dim]{short_label}:[/dim] {rec_short.get(s.recommendation, s.recommendation)}")

        table.add_row(
            report.meeting_name,
            cell("Qualifying"),
            cell("Race"),
            cell("Sprint Qualifying"),
            cell("Sprint"),
            "  ".join(recs),
        )

    console.print(table)
    console.print()


# ── Progress ──────────────────────────────────────────────────────────────────

def make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    )
