"""
Flask web app for F1 Watchability.
Run with: python -m f1_watchability.web.app
or:        flask --app f1_watchability.web.app run
"""

from __future__ import annotations

import logging
from flask import Flask, jsonify, render_template, request

from ..config_loader import load_config
from .service import get_calendar, score_weekend, weekend_report_to_dict

logger = logging.getLogger(__name__)

app = Flask(__name__)
_config = load_config()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/calendar")
def api_calendar():
    year = request.args.get("year", 2024, type=int)
    try:
        calendar = get_calendar(year)
        return jsonify({"ok": True, "year": year, "meetings": calendar})
    except Exception as e:
        logger.exception("Error fetching calendar")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/score")
def api_score():
    year        = request.args.get("year", 2024, type=int)
    meeting_key = request.args.get("meeting_key", type=int)

    if meeting_key is None:
        return jsonify({"ok": False, "error": "meeting_key is required"}), 400

    try:
        report = score_weekend(meeting_key=meeting_key, year=year, config=_config)
        if report is None:
            return jsonify({"ok": False, "error": "No completed sessions found for this race weekend."}), 404
        return jsonify({"ok": True, "report": weekend_report_to_dict(report)})
    except Exception as e:
        logger.exception("Error scoring weekend")
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=True, port=5000)
