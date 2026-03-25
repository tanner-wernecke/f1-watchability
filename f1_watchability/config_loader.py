"""
Config loader with deep-merge support for circuit overrides.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "weights.yaml"


def load_config(path: str | Path | None = None) -> dict:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path) as f:
        return yaml.safe_load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def get_session_config(config: dict, session_type_key: str, circuit: str) -> tuple[dict, bool]:
    """
    Return (session_cfg, override_applied) for the given session type and circuit.

    session_type_key: "race" | "qualifying" | "sprint_race" | "sprint_qualifying"
    circuit: circuit_short_name from OpenF1 (e.g. "Monaco")
    """
    base = config["defaults"].get(session_type_key, {})

    overrides = config.get("circuit_overrides", {})
    circuit_override = overrides.get(circuit, {}).get(session_type_key, {})

    if circuit_override:
        return _deep_merge(base, circuit_override), True
    return copy.deepcopy(base), False
