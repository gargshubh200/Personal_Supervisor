import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv
from langfuse import observe, get_client, propagate_attributes

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError, ServerError

gemini_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    retry=retry_if_exception_type((ClientError, ServerError)),
    reraise=True
)

load_dotenv()

APP_LOG_PATH = Path(__file__).parent / "applications_log.json"


def _load_log() -> List[Dict[str, Any]]:
    if not APP_LOG_PATH.exists():
        return []
    with open(APP_LOG_PATH, "r", encoding="utf-8") as f:
        raw = f.read().strip()
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []


def _save_log(log_data: List[Dict[str, Any]]) -> None:
    with open(APP_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)


# ------------------------------------------------------------------
# Personal Operations Tools & Tracing
# ------------------------------------------------------------------
@gemini_retry
@observe(name="Personal Ops: Log Application")
def log_tailored_application(company: str, role: str, tailored_summary: str, matched_keywords: List[str]) -> Dict[
    str, Any]:
    """Logs a newly tailored job application into the local tracking database."""
    log = _load_log()

    entry = {
        "id": f"APP-{len(log) + 1:03d}",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "company": company,
        "role": role,
        "status": "Tailored & Ready",
        "tailored_summary": tailored_summary,
        "matched_keywords": matched_keywords
    }

    log.append(entry)
    _save_log(log)

    langfuse = get_client()
    langfuse.update_current_span(
        metadata={"application_id": entry["id"], "company": company, "role": role}
    )
    return entry

@gemini_retry
@observe(name="Personal Ops: Daily Standup Summary")
def generate_daily_standup_report(applications: List[Dict[str, Any]] = None) -> str:
    """
    Generates a daily metrics digest of the 4-month job switch pipeline.

    If `applications` is provided (e.g. today's Firestore application records from
    db_manager.get_daily_standup_summary()), the report is built from that live data.
    Otherwise it falls back to the local applications_log.json file (used by the
    standalone `log_tailored_application()` tracker / this module's own test run).
    """
    log = applications if applications is not None else _load_log()
    total_apps = len(log)

    if total_apps == 0:
        return "📊 **Personal Ops Daily Standup:** 0 applications logged yet. System ready for daily scraping run."

    recent = log[-3:]  # Last 3 applications

    report = [
        "==================================================",
        "PERSONAL EXECUTIVE OS - DAILY STANDUP DIGEST",
        f"Date: {datetime.now().strftime('%B %d, %Y')}",
        "==================================================",
        f"Total Tailored Applications: {total_apps}",
        "--------------------------------------------------",
        "Recent Tailored Roles:"
    ]

    for app in recent:
        identifier = app.get("id") or app.get("company", "")[:3].upper()
        role = app.get("role") or app.get("title", "")
        report.append(f"  • [{identifier}] {app.get('company', '')} - {role} (Status: {app.get('status', '')})")

    report.append("==================================================")
    return "\n".join(report)


if __name__ == "__main__":
    # Test logging a sample application
    log_tailored_application(
        company="Stripe",
        role="Forward Deployed Engineer",
        tailored_summary="Software Engineer specializing in applied AI and scalable platform architecture.",
        matched_keywords=["Forward Deployed Engineer", "Python", "Systems Architecture"]
    )

    # Print generated daily standup report
    print(generate_daily_standup_report())