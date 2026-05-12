import argparse
import hashlib
import json
import logging
import os
import smtplib
import sqlite3
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from deepdiff import DeepDiff
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)


def setup_logging(log_path: Path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    file_handler = RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


class ChangeMonitor:
    def __init__(self, target_url: str, db_path: Path, log_path: Path):
        self.target_url = target_url.rstrip("/")
        self.db_path = Path(db_path)
        self.log_path = Path(log_path)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.scan_interval_minutes = int(os.getenv("SCAN_INTERVAL_MINUTES", "5"))
        self.request_timeout = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
        self.user_agent = os.getenv(
            "REQUEST_USER_AGENT",
            "WebsiteChangeDetector/1.0 (+https://www.voyage.benin.bj monitor)",
        )
        self.asset_timeout = int(os.getenv("ASSET_TIMEOUT_SECONDS", "20"))
        self.max_assets_per_type = int(os.getenv("MAX_ASSETS_PER_TYPE", "20"))
        self.fetch_linked_assets = os.getenv("FETCH_LINKED_ASSETS", "true").lower() == "true"
        self.ignore_system_proxies = (
            os.getenv("IGNORE_SYSTEM_PROXIES", "true").lower() == "true"
        )
        self.record_scans_without_changes = (
            os.getenv("RECORD_SCANS_WITHOUT_CHANGES", "true").lower() == "true"
        )
        self.email_on_no_change = (
            os.getenv("EMAIL_ON_NO_CHANGE", "false").lower() == "true"
        )
        self.scheduler = None
        self._ensure_database()

    def _get_connection(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_database(self):
        schema_path = BASE_DIR / "database" / "schema.sql"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as connection:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.commit()

    def start_scheduler(self):
        if self.scheduler and self.scheduler.running:
            return

        self.scheduler = BackgroundScheduler(timezone=os.getenv("TIMEZONE", "Europe/Paris"))
        self.scheduler.add_job(
            func=self.run_scan,
            trigger="interval",
            minutes=self.scan_interval_minutes,
            id="website_scan_job",
            max_instances=1,
            coalesce=True,
            kwargs={"trigger": "scheduler"},
        )
        self.scheduler.start()
        self.logger.info(
            "Scheduler started for %s every %s minutes.",
            self.target_url,
            self.scan_interval_minutes,
        )

    def fetch_current_state(self):
        response = self._create_session().get(
            self.target_url,
            headers={"User-Agent": self.user_agent},
            timeout=self.request_timeout,
        )
        response.raise_for_status()

        html = response.text
        snapshot = self._build_snapshot(html)
        content_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()

        return {
            "url": response.url,
            "status_code": response.status_code,
            "html": html,
            "snapshot": snapshot,
            "content_hash": content_hash,
        }

    def _build_snapshot(self, html: str):
        soup = BeautifulSoup(html, "html.parser")
        base_url = self.target_url

        title = soup.title.get_text(strip=True) if soup.title else ""
        meta_descriptions = [
            meta.get("content", "").strip()
            for meta in soup.find_all("meta")
            if meta.get("name", "").lower() == "description"
        ]

        forms = []
        for form in soup.find_all("form"):
            fields = []
            for field in form.find_all(["input", "select", "textarea", "button"]):
                fields.append(
                    {
                        "tag": field.name,
                        "type": field.get("type", ""),
                        "name": field.get("name", ""),
                        "id": field.get("id", ""),
                        "text": field.get_text(" ", strip=True) if field.name == "button" else "",
                        "placeholder": field.get("placeholder", ""),
                    }
                )
            forms.append(
                {
                    "action": form.get("action", ""),
                    "method": form.get("method", "get").lower(),
                    "id": form.get("id", ""),
                    "classes": sorted(form.get("class", [])),
                    "fields": fields,
                }
            )

        stylesheets = sorted(
            {
                urljoin(base_url, link.get("href", "").strip())
                for link in soup.find_all("link")
                if link.get("rel") and "stylesheet" in link.get("rel") and link.get("href")
            }
        )
        scripts_external = sorted(
            {
                urljoin(base_url, script.get("src", "").strip())
                for script in soup.find_all("script")
                if script.get("src")
            }
        )
        images = sorted(
            {
                urljoin(base_url, image.get("src", "").strip()) + f" | alt={image.get('alt', '').strip()}"
                for image in soup.find_all("img")
                if image.get("src")
            }
        )

        snapshot = {
            "title": title,
            "meta_descriptions": meta_descriptions,
            "texts": sorted(
                {
                    text.strip()
                    for text in soup.stripped_strings
                    if text and len(text.strip()) > 1
                }
            ),
            "headings": [
                {"tag": tag.name, "text": tag.get_text(" ", strip=True)}
                for tag in soup.find_all(["h1", "h2", "h3", "h4"])
            ],
            "links": sorted(
                {
                    f"{a.get_text(' ', strip=True)} -> {a.get('href', '').strip()}"
                    for a in soup.find_all("a")
                    if a.get("href")
                }
            ),
            "buttons": sorted(
                {
                    f"{button.get_text(' ', strip=True)} | {button.get('type', 'button')}"
                    for button in soup.find_all("button")
                }
            ),
            "images": images,
            "stylesheets": stylesheets,
            "scripts_external": scripts_external,
            "inline_styles": sorted(
                {
                    style.get_text("\n", strip=True)
                    for style in soup.find_all("style")
                    if style.get_text(strip=True)
                }
            ),
            "inline_scripts": sorted(
                {
                    script.get_text("\n", strip=True)
                    for script in soup.find_all("script")
                    if not script.get("src") and script.get_text(strip=True)
                }
            ),
            "forms": forms,
            "html_structure": [
                element.name
                for element in soup.find_all()
                if element.name not in {"script", "style"}
            ],
            "html_length": len(html),
        }

        if self.fetch_linked_assets:
            snapshot["asset_fingerprints"] = {
                "stylesheets": self._collect_asset_fingerprints(stylesheets, "text/css"),
                "scripts": self._collect_asset_fingerprints(scripts_external, "application/javascript"),
                "images": self._collect_asset_fingerprints(
                    [item.split(" | alt=")[0] for item in images],
                    "image",
                ),
            }

        return snapshot

    def _collect_asset_fingerprints(self, urls: list[str], expected_type_hint: str):
        fingerprints = []
        session = self._create_session()
        for asset_url in urls[: self.max_assets_per_type]:
            try:
                response = session.get(
                    asset_url,
                    headers={"User-Agent": self.user_agent},
                    timeout=self.asset_timeout,
                )
                response.raise_for_status()
                fingerprints.append(
                    {
                        "url": asset_url,
                        "content_type": response.headers.get("Content-Type", expected_type_hint),
                        "content_length": response.headers.get("Content-Length", ""),
                        "etag": response.headers.get("ETag", ""),
                        "last_modified": response.headers.get("Last-Modified", ""),
                        "hash": hashlib.sha256(response.content).hexdigest(),
                    }
                )
            except Exception as exc:
                self.logger.warning("Unable to fingerprint asset %s: %s", asset_url, exc)
                fingerprints.append(
                    {
                        "url": asset_url,
                        "error": str(exc),
                    }
                )
        return fingerprints

    def _create_session(self):
        session = requests.Session()
        if self.ignore_system_proxies:
            # Some Windows environments inject proxy variables that break direct HTTPS access.
            session.trust_env = False
        return session

    def _get_last_version(self):
        with self._get_connection() as connection:
            return connection.execute(
                """
                SELECT id, snapshot_json, raw_html, content_hash, created_at
                FROM versions
                WHERE url = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.target_url,),
            ).fetchone()

    def _insert_version(self, state: dict[str, Any]):
        created_at = datetime.now().isoformat(timespec="seconds")
        with self._get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO versions (url, created_at, status_code, content_hash, snapshot_json, raw_html)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.target_url,
                    created_at,
                    state["status_code"],
                    state["content_hash"],
                    json.dumps(state["snapshot"], ensure_ascii=False),
                    state["html"],
                ),
            )
            connection.commit()
            return cursor.lastrowid, created_at

    def _insert_change(
        self,
        version_id: int,
        previous_version_id: int,
        diff: DeepDiff,
        summary: str,
        diff_text: str,
        change_types: str,
    ):
        detected_at = datetime.now().isoformat(timespec="seconds")
        diff_json = diff.to_json(indent=2, ensure_ascii=False)

        with self._get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO changes (
                    version_id,
                    previous_version_id,
                    url,
                    detected_at,
                    change_types,
                    summary,
                    diff_json,
                    diff_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    previous_version_id,
                    self.target_url,
                    detected_at,
                    change_types,
                    summary,
                    diff_json,
                    diff_text,
                ),
            )
            connection.commit()
            return cursor.lastrowid, detected_at

    def _insert_scan(
        self,
        status: str,
        message: str,
        duration_ms: int,
        version_id=None,
        change_id=None,
    ):
        scanned_at = datetime.now().isoformat(timespec="seconds")
        with self._get_connection() as connection:
            connection.execute(
                """
                INSERT INTO scans (
                    scanned_at,
                    url,
                    status,
                    message,
                    duration_ms,
                    version_id,
                    change_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scanned_at,
                    self.target_url,
                    status,
                    message,
                    duration_ms,
                    version_id,
                    change_id,
                ),
            )
            connection.commit()

    def _classify_change_types(self, diff_map: dict[str, Any]):
        types = set()
        diff_text = json.dumps(diff_map, ensure_ascii=False).lower()

        if "forms" in diff_text or "buttons" in diff_text:
            types.add("Formulaire / bouton")
        if "inline_styles" in diff_text or "stylesheets" in diff_text:
            types.add("CSS")
        if "inline_scripts" in diff_text or "scripts_external" in diff_text:
            types.add("JavaScript")
        if "images" in diff_text:
            types.add("Image")
        if "texts" in diff_text or "title" in diff_text or "meta_descriptions" in diff_text:
            types.add("Texte")
        if "html_structure" in diff_text or "values_changed" in diff_map:
            types.add("HTML / structure")

        return ", ".join(sorted(types)) if types else "Changement général"

    def _render_diff_text(self, diff_map: dict[str, Any]):
        lines = []

        for section in ("dictionary_item_added", "dictionary_item_removed"):
            items = diff_map.get(section, [])
            if items:
                label = "Ajout" if section.endswith("added") else "Suppression"
                for item in items:
                    lines.append(f"{label}: {item}")

        for section in ("iterable_item_added", "iterable_item_removed"):
            items = diff_map.get(section, {})
            label = "Ajout" if section.endswith("added") else "Suppression"
            for path, value in items.items():
                lines.append(f"{label}: {path}")
                lines.append(f"Valeur: {self._stringify(value)}")

        for path, values in diff_map.get("values_changed", {}).items():
            lines.append(f"Modification: {path}")
            lines.append(f"Ancienne valeur: {self._stringify(values.get('old_value'))}")
            lines.append(f"Nouvelle valeur: {self._stringify(values.get('new_value'))}")

        for path, values in diff_map.get("type_changes", {}).items():
            lines.append(f"Type modifié: {path}")
            lines.append(f"Ancienne valeur: {self._stringify(values.get('old_value'))}")
            lines.append(f"Nouvelle valeur: {self._stringify(values.get('new_value'))}")

        return "\n".join(lines[:120]) if lines else "Aucun détail exploitable."

    def _build_summary(self, diff_map: dict[str, Any], change_types: str):
        counters = []
        for key, value in diff_map.items():
            if isinstance(value, dict):
                counters.append(f"{key}: {len(value)}")
            elif isinstance(value, list):
                counters.append(f"{key}: {len(value)}")

        counter_text = ", ".join(counters) if counters else "modification détectée"
        return f"{change_types} | {counter_text}"

    def _stringify(self, value):
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _send_email_alert(self, detected_at: str, summary: str, diff_text: str):
        email_enabled = os.getenv("EMAIL_ENABLED", "true").lower() == "true"
        if not email_enabled:
            self.logger.info("Email notifications are disabled.")
            return {"attempted": False, "sent": False, "reason": "EMAIL_ENABLED=false"}

        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "")
        smtp_password = os.getenv("SMTP_PASSWORD", "")
        sender = os.getenv("EMAIL_FROM", smtp_user)
        recipient = os.getenv("EMAIL_TO", "lordelesly@gmail.com")

        if not smtp_user or not smtp_password or not sender or not recipient:
            self.logger.warning("SMTP configuration is incomplete. Email alert skipped.")
            return {"attempted": False, "sent": False, "reason": "SMTP configuration incomplete"}

        message = MIMEMultipart("alternative")
        message["Subject"] = f"[Website Change Detector] Modification détectée sur {self.target_url}"
        message["From"] = sender
        message["To"] = recipient

        text_body = f"""
Date du changement : {detected_at}
URL surveillée : {self.target_url}

Résumé :
{summary}

Différences :
{diff_text}
        """.strip()

        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; color: #10233f;">
                <h2>Website Change Detector</h2>
                <p><strong>Date du changement :</strong> {detected_at}</p>
                <p><strong>URL surveillée :</strong> <a href="{self.target_url}">{self.target_url}</a></p>
                <p><strong>Résumé :</strong> {summary}</p>
                <p><strong>Détails :</strong></p>
                <pre style="white-space: pre-wrap; background: #f5f7fb; padding: 16px; border-radius: 12px;">{diff_text}</pre>
            </body>
        </html>
        """.strip()

        message.attach(MIMEText(text_body, "plain", "utf-8"))
        message.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.sendmail(sender, recipient, message.as_string())

        self.logger.info("Email alert sent to %s", recipient)
        return {"attempted": True, "sent": True, "recipient": recipient}


    def _send_scan_email(
        self,
        detected_at: str,
        summary: str,
        diff_text: str,
        *,
        change_detected: bool,
    ):
        if not change_detected and not self.email_on_no_change:
            self.logger.info("No-change email notifications are disabled.")
            return {
                "attempted": False,
                "sent": False,
                "type": "aucun changement",
                "reason": "EMAIL_ON_NO_CHANGE=false",
            }

        label = "Modification detectee" if change_detected else "Aucun changement detecte"
        date_label = "Date du changement" if change_detected else "Date du scan"
        formatted_summary = f"{label} | {summary}"
        formatted_diff = f"{date_label}: {detected_at}\n{diff_text}"
        email_result = self._send_email_alert(detected_at, formatted_summary, formatted_diff)
        email_result["type"] = "changement" if change_detected else "aucun changement"
        return email_result

    def run_scan(self, trigger="manual"):
        start_time = time.perf_counter()
        self.logger.info("Starting %s scan for %s", trigger, self.target_url)

        try:
            current_state = self.fetch_current_state()
            last_version = self._get_last_version()

            if not last_version:
                version_id, _ = self._insert_version(current_state)
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                message = "Version initiale enregistrée."
                self._insert_scan("success", message, duration_ms, version_id=version_id)
                return {
                    "status": "success",
                    "change_detected": False,
                    "message": message,
                    "version_id": version_id,
                }

            if last_version["content_hash"] == current_state["content_hash"]:
                duration_ms = int((time.perf_counter() - start_time) * 1000)
                message = "Aucun changement détecté."
                email_result = self._send_scan_email(
                    datetime.now().isoformat(timespec="seconds"),
                    "Le site surveille n'a subi aucune modification depuis la derniere version.",
                    "Le contenu analyse est identique a la derniere version sauvegardee.",
                    change_detected=False,
                )
                print(f"Mail envoye : {'oui' if email_result.get('sent') else 'non'}")
                print(f"Type : {email_result.get('type', 'inconnu')}")
                print(f"Destinataire : {email_result.get('recipient', 'non defini')}")
                print(f"Raison : {email_result.get('reason', 'envoi effectue')}")
                if self.record_scans_without_changes:
                    self._insert_scan(
                        "success",
                        message,
                        duration_ms,
                        version_id=last_version["id"],
                    )
                return {
                    "status": "success",
                    "change_detected": False,
                    "email_sent": email_result.get("sent", False),
                    "email_type": email_result.get("type"),
                    "email_recipient": email_result.get("recipient"),
                    "email_reason": email_result.get("reason"),
                    "message": message,
                    "version_id": last_version["id"],
                }

            previous_snapshot = json.loads(last_version["snapshot_json"])
            diff = DeepDiff(
                previous_snapshot,
                current_state["snapshot"],
                ignore_order=True,
                verbose_level=2,
            )
            diff_map = json.loads(diff.to_json(ensure_ascii=False))
            change_types = self._classify_change_types(diff_map)
            summary = self._build_summary(diff_map, change_types)
            diff_text = self._render_diff_text(diff_map)

            version_id, _ = self._insert_version(current_state)
            change_id, detected_at = self._insert_change(
                version_id=version_id,
                previous_version_id=last_version["id"],
                diff=diff,
                summary=summary,
                diff_text=diff_text,
                change_types=change_types,
            )
            email_result = self._send_scan_email(
                detected_at,
                summary,
                diff_text,
                change_detected=True,
            )
            print(f"Mail envoye : {'oui' if email_result.get('sent') else 'non'}")
            print(f"Type : {email_result.get('type', 'inconnu')}")
            print(f"Destinataire : {email_result.get('recipient', 'non defini')}")
            print(f"Raison : {email_result.get('reason', 'envoi effectue')}")

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            message = "Changement détecté, enregistré et notifié."
            self._insert_scan(
                "success",
                message,
                duration_ms,
                version_id=version_id,
                change_id=change_id,
            )
            return {
                "status": "success",
                "change_detected": True,
                "email_sent": email_result.get("sent", False),
                "email_type": email_result.get("type"),
                "email_recipient": email_result.get("recipient"),
                "email_reason": email_result.get("reason"),
                "message": message,
                "version_id": version_id,
                "change_id": change_id,
                "summary": summary,
            }
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            message = f"Erreur durant le scan : {exc}"
            self.logger.exception(message)
            self._insert_scan("error", message, duration_ms)
            return {
                "status": "error",
                "change_detected": False,
                "message": message,
            }


def build_monitor_from_env():
    db_path = BASE_DIR / "database" / "website_monitor.db"
    log_path = BASE_DIR / "logs" / "website_monitor.log"
    setup_logging(log_path)
    return ChangeMonitor(
        target_url=os.getenv("TARGET_URL", "https://www.voyage.benin.bj"),
        db_path=db_path,
        log_path=log_path,
    )


def main():
    parser = argparse.ArgumentParser(description="Website Change Detector")
    parser.add_argument("--once", action="store_true", help="Execute a single scan")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run the scanner continuously using the interval from .env",
    )
    args = parser.parse_args()

    monitor = build_monitor_from_env()

    if args.once or not args.loop:
        result = monitor.run_scan(trigger="cli")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    monitor.start_scheduler()
    print(
        f"Continuous monitoring started for {monitor.target_url} every "
        f"{monitor.scan_interval_minutes} minutes."
    )

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        if monitor.scheduler:
            monitor.scheduler.shutdown()
        print("Monitoring stopped.")


if __name__ == "__main__":
    main()
