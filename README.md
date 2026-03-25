# 🏎️ F1 Watchability

Tells you which F1 sessions are worth watching — and whether to watch in full, catch the highlights, or skip — without spoiling why.

Pulls data from [OpenF1](https://openf1.org/) (free, no API key needed) and scores each session using configurable, rule-based weights.

---

## Installation

```bash
# Option A — install as a CLI command (recommended)
pip install -e .
f1watch --help

# Option B — run directly
pip install -r requirements.txt
python main.py --help
```

---

## Usage

```bash
# List all race weekends in the current season
f1watch --list

# Score a single race weekend (spoiler-safe)
f1watch --race "Monaco"

# After watching — see the full reasoning
f1watch --race "Monaco" --spoilers

# Full season overview
f1watch

# Different year
f1watch --year 2023 --race "Bahrain"
```

---

## Recommendations

| Session | Tiers |
|---------|-------|
| Race | ✅ Watch Full / 🟡 Race in 30 / 📋 Watch Highlights |
| Sprint | ✅ Watch Full / 🟡 Race in 30 / 📋 Watch Highlights |
| Qualifying | ✅ Watch Full / 📋 Watch Highlights |
| Sprint Qualifying | ✅ Watch Full / 📋 Watch Highlights |

---

## Scoring factors

**Race / Sprint**
| Factor | Default weight | What it measures |
|--------|---------------|-----------------|
| Close finish | 20% | P1–P3 gap at the flag |
| Overtakes | 20% | On-track position changes in top 10 |
| Grid vs result | 15% | Average grid-to-finish position change |
| Safety car | 10% | SC / VSC deployments |
| Pit stop variety | 10% | Range of strategies used |
| Team diversity | 10% | Different constructors in top 10 |
| DNF drama | 10% | Notable retirements (top 10 championship / top 4 constructor / top 10 grid starter) |
| Wet weather | 5% | Rain and changing conditions |

**Qualifying / Sprint Qualifying**
| Factor | Default weight | What it measures |
|--------|---------------|-----------------|
| Close gaps | 40% | Pole–P3 gap |
| Grid shuffle | 30% | Non-front-runner teams in top 5 |
| DNF drama | 15% | Notable drivers knocked out early |
| Wet weather | 15% | Rain / mixed conditions |

**Bonuses** applied after base score:
- +15 Last lap lead change
- +10 Sub-1-second P1–P2 finish
- +10 Championship lead changes hands
- +8 Red flag and restart
- +6 Multiple SC/VSC deployments

**Penalties** applied after base score:
- −12 Dominant leader (20+ sec gap by lap 10)
- −10 No on-track position changes in top 10
- −8 Top 5 result identical to starting grid

---

## Customising weights

All scoring is configured in `config/weights.yaml`. You can:
- Adjust factor weights (must sum to 1.0 per session type)
- Change Watch Full / Race in 30 thresholds
- Add circuit-specific overrides for tracks that behave differently

Circuit overrides support partial configuration — if you only override thresholds, weights fall back to the defaults automatically.

**Currently overridden circuits:** Monaco, Singapore, Baku, Monza, Spa

Any new circuit not in the overrides list automatically uses the defaults.

---

## Notes

- Data coverage: 2023 season onwards (OpenF1 limitation)
- Rate limit: free tier is 3 req/s — the app fetches sequentially and displays a progress spinner
- Grid position data: cross-session enrichment (qualifying → race) is on the roadmap; currently falls back to a neutral score if unavailable
- DNF detection: inferred from classified/unclassified status in position data — not always 100% accurate for very early retirements

---

## Roadmap

- [ ] Enrich race `grid_position` from the qualifying session of the same weekend
- [ ] Cache completed session data locally to avoid re-fetching
- [ ] Season-end weight calibration against community ratings (r/formula1 rate-the-race threads)
- [ ] Web UI
- [ ] Notifications: "This weekend looks 🔥, avoid social media"
