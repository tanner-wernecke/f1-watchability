"""
Flask web app for F1 Watchability.
Run with: PYTHONPATH=. python3 -m f1_watchability.web.app
"""

from __future__ import annotations

import logging
from flask import Flask, jsonify, render_template, request

from ..config_loader import load_config
from .service import get_calendar, score_weekend, weekend_report_to_dict
from . import cache as cache_store

logger = logging.getLogger(__name__)

app = Flask(__name__)
_config = load_config()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/calendar")
def api_calendar():
    year = request.args.get("year", 2026, type=int)
    try:
        calendar = get_calendar(year)
        return jsonify({"ok": True, "year": year, "meetings": calendar})
    except Exception as e:
        logger.exception("Error fetching calendar")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/score")
def api_score():
    year        = request.args.get("year", 2026, type=int)
    meeting_key = request.args.get("meeting_key", type=int)

    if meeting_key is None:
        return jsonify({"ok": False, "error": "meeting_key is required"}), 400

    try:
        report = score_weekend(meeting_key=meeting_key, year=year, config=_config)
        if report is None:
            return jsonify({"ok": False, "error": "No completed sessions found for this race weekend."}), 404

        # Handle both live-scored and cached reports
        report_dict = report.to_dict() if hasattr(report, "to_dict") else weekend_report_to_dict(report)
        return jsonify({"ok": True, "report": report_dict})
    except Exception as e:
        logger.exception("Error scoring weekend")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cache")
def api_cache_info():
    """Show what's currently in the cache — useful for debugging."""
    return jsonify({"ok": True, "entries": cache_store.cache_info()})


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Clear all cached results. Use after changing scoring logic."""
    count = cache_store.clear_all()
    return jsonify({"ok": True, "cleared": count})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=True, port=5000)
