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
        return jsonify({"ok": True, "report": weekend_report_to_dict(report)})
    except Exception as e:
        logger.exception("Error scoring weekend")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug")
def api_debug():
    year        = request.args.get("year", 2026, type=int)
    meeting_key = request.args.get("meeting_key", type=int)
    if meeting_key is None:
        return jsonify({"ok": False, "error": "meeting_key is required"}), 400
    try:
        from ..fetcher import get_meetings_with_sessions
        meetings = get_meetings_with_sessions(year=year)
        sessions = meetings.get(meeting_key, [])
        return jsonify({
            "ok": True,
            "meeting_key": meeting_key,
            "year": year,
            "total_meetings": len(meetings),
            "sessions_found": len(sessions),
            "sessions": [
                {
                    "session_key":  s.session_key,
                    "session_name": s.session_name,
                    "session_type": s.session_type,
                    "date_start":   s.date_start,
                }
                for s in sessions
            ],
        })
    except Exception as e:
        logger.exception("Debug error")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/debug/score")
def api_debug_score():
    year        = request.args.get("year", 2026, type=int)
    session_key = request.args.get("session_key", type=int)
    if session_key is None:
        return jsonify({"ok": False, "error": "session_key is required"}), 400
    try:
        from ..fetcher import fetch_session_data, get_scorable_sessions
        sessions = get_scorable_sessions(year=year)
        session_info = next((s for s in sessions if s.session_key == session_key), None)
        if session_info is None:
            return jsonify({"ok": False, "error": f"Session {session_key} not found"}), 404

        raw = fetch_session_data(session_info)
        return jsonify({
            "ok": True,
            "session_type": session_info.session_type,
            "session_name": session_info.session_name,
            "drivers_count": len(raw.drivers),
            "position_data_count": len(raw.position_data),
            "interval_data_count": len(raw.interval_data),
            "race_control_count": len(raw.race_control),
            "pit_stops_count": len(raw.pit_stops),
            "weather_count": len(raw.weather_samples),
            "top5_drivers": [
                {"pos": d.finish_position, "name": d.full_name, "team": d.team_name, "classified": d.is_classified}
                for d in sorted(raw.drivers, key=lambda x: x.finish_position)[:5]
            ],
        })
    except Exception as e:
        logger.exception("Debug score error")
        return jsonify({"ok": False, "error": str(e), "type": type(e).__name__}), 500
def api_cache_info():
    from . import cache as cache_store
    return jsonify({"ok": True, "entries": cache_store.cache_info()})


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    from . import cache as cache_store
    count = cache_store.clear_all()
    return jsonify({"ok": True, "cleared": count})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(debug=True, port=5000)
