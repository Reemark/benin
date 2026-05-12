import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, url_for

from monitor import ChangeMonitor, setup_logging


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "database" / "website_monitor.db"
LOG_PATH = BASE_DIR / "logs" / "website_monitor.log"

load_dotenv(BASE_DIR / ".env")
setup_logging(LOG_PATH)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-detector-secret")

monitor = ChangeMonitor(
    target_url=os.getenv("TARGET_URL", "https://www.voyage.benin.bj"),
    db_path=DB_PATH,
    log_path=LOG_PATH,
)


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def bootstrap_database():
    schema_path = BASE_DIR / "database" / "schema.sql"
    with get_db_connection() as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.commit()


def start_scheduler():
    if os.getenv("ENABLE_SCHEDULER", "true").lower() != "true":
        logger.info("Scheduler disabled by configuration.")
        return

    if app.debug and os.getenv("WERKZEUG_RUN_MAIN") != "true":
        return

    if getattr(app, "_scheduler_started", False):
        return

    monitor.start_scheduler()
    app._scheduler_started = True
    logger.info("Background scheduler started from Flask application.")


@app.template_filter("datetimeformat")
def datetimeformat(value):
    if not value:
        return "-"
    parsed = datetime.fromisoformat(value)
    return parsed.strftime("%d/%m/%Y %H:%M:%S")


@app.route("/")
def dashboard():
    with get_db_connection() as connection:
        stats = {
            "total_changes": connection.execute(
                "SELECT COUNT(*) AS count FROM changes"
            ).fetchone()["count"],
            "total_versions": connection.execute(
                "SELECT COUNT(*) AS count FROM versions"
            ).fetchone()["count"],
            "last_scan": connection.execute(
                "SELECT scanned_at, status, message FROM scans ORDER BY id DESC LIMIT 1"
            ).fetchone(),
            "last_change": connection.execute(
                "SELECT detected_at, summary FROM changes ORDER BY id DESC LIMIT 1"
            ).fetchone(),
        }

        recent_changes = connection.execute(
            """
            SELECT id, detected_at, change_types, summary, diff_text, diff_json, url
            FROM changes
            ORDER BY id DESC
            LIMIT 25
            """
        ).fetchall()

        recent_scans = connection.execute(
            """
            SELECT scanned_at, status, message, duration_ms
            FROM scans
            ORDER BY id DESC
            LIMIT 10
            """
        ).fetchall()

    return render_template(
        "dashboard.html",
        stats=stats,
        recent_changes=recent_changes,
        recent_scans=recent_scans,
        target_url=monitor.target_url,
        scan_interval=monitor.scan_interval_minutes,
    )


@app.route("/scan-now", methods=["POST"])
def scan_now():
    result = monitor.run_scan(trigger="manual")

    if result["status"] == "success":
        if result["change_detected"]:
            flash("Changement détecté et enregistré. Une alerte email a été envoyée.", "success")
        else:
            flash("Scan terminé : aucune modification détectée.", "info")
    else:
        flash(f"Le scan a échoué : {result['message']}", "danger")

    return redirect(url_for("dashboard"))


@app.route("/api/status")
def api_status():
    with get_db_connection() as connection:
        last_scan = connection.execute(
            "SELECT scanned_at, status, message FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_change = connection.execute(
            "SELECT detected_at, change_types, summary FROM changes ORDER BY id DESC LIMIT 1"
        ).fetchone()

    return jsonify(
        {
            "target_url": monitor.target_url,
            "scheduler_enabled": os.getenv("ENABLE_SCHEDULER", "true").lower() == "true",
            "scan_interval_minutes": monitor.scan_interval_minutes,
            "last_scan": dict(last_scan) if last_scan else None,
            "last_change": dict(last_change) if last_change else None,
        }
    )


bootstrap_database()
start_scheduler()


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host=host, port=port, debug=debug)
