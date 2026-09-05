import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from dotenv import load_dotenv
from langfuse import observe, get_client, propagate_attributes

load_dotenv()

APP_LOG_PATH = Path(__file__).parent / "applications_log.json"


def _load_log() -> List[Dict[str, Any]]:
    if not APP_LOG_PATH.exists():
        return []
    with open(APP_LOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_log(log_data: List[Dict[str, Any]]) -> None:
    with open(APP_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)


# ------------------------------------------------------------------
# Personal Operations Tools & Tracing
# ------------------------------------------------------------------

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


@observe(name="Personal Ops: Daily Standup Summary")
def generate_daily_standup_report() -> str:
    """Generates a daily metrics digest of the 4-month job switch pipeline."""
    log = _load_log()
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
        report.append(f"  • [{app['id']}] {app['company']} - {app['role']} (Status: {app['status']})")

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