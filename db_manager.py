import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from google.cloud import firestore

# Rich Application Lifecycle States
ApplicationStatus = Literal[
    "DISCOVERED",
    "ANALYZED",
    "SKIPPED",
    "SHORTLISTED",
    "TAILORED",
    "READY_TO_APPLY",
    "APPLIED",
    "OA",             # Online Assessment
    "INTERVIEW",
    "REJECTED",
    "WITHDRAWN",
    "OFFER"
]


class DatabaseManager:
    def __init__(self, app_collection="job_applications", lead_collection="hiring_manager_leads"):
        self.db = firestore.Client(project="career-os-project")
        self.app_collection = self.db.collection(app_collection)
        self.lead_collection = self.db.collection(lead_collection)

    def _generate_job_id(self, company: str, title: str) -> str:
        """Generates a unique deterministic document key based on company and title."""
        raw_str = f"{company.lower().strip()}_{title.lower().strip()}"
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

    def _generate_lead_id(self, post_url: str, manager_name: str = "", company: str = "") -> str:
        """Generates a unique deterministic document key for hiring manager posts."""
        raw_str = post_url.strip() if post_url else f"{manager_name.lower().strip()}_{company.lower().strip()}"
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

    # --------------------------------------------------------------------------
    # APPLICATION LIFECYCLE MANAGEMENT
    # --------------------------------------------------------------------------
    def is_job_processed(self, company: str, title: str) -> bool:
        """Checks if a job lead was already scraped and analyzed in a prior run."""
        doc_id = self._generate_job_id(company, title)
        return self.app_collection.document(doc_id).get().exists

    def save_application_record(
            self,
            company: str,
            title: str,
            location: str,
            match_score: int,
            core_capability_score: int,
            strategy: str,
            decision: str,
            status: ApplicationStatus,
            dealbreaker_triggered: bool = False,
            dealbreaker_reason: Optional[str] = None,
            drive_resume_link: Optional[str] = None,
            drive_cover_letter_link: Optional[str] = None,
            job_url: str = ""
    ) -> str:
        """Persists or updates an application record with full lifecycle state & audit trail."""
        doc_id = self._generate_job_id(company, title)
        doc_ref = self.app_collection.document(doc_id)
        now_str = datetime.utcnow().isoformat()

        existing_doc = doc_ref.get()
        status_history = []

        if existing_doc.exists:
            existing_data = existing_doc.to_dict()
            status_history = existing_data.get("status_history", [])

        # Append new state transition if state changed or new record
        if not status_history or status_history[-1].get("status") != status:
            status_history.append({
                "status": status,
                "timestamp": now_str,
                "note": f"System updated status to {status} via Supervisor Agent pipeline."
            })

        payload = {
            "job_id": doc_id,
            "company": company,
            "title": title,
            "location": location,
            "match_score": match_score,
            "core_capability_score": core_capability_score,
            "strategy_priority": strategy,
            "decision": decision,
            "status": status,
            "dealbreaker_triggered": dealbreaker_triggered,
            "dealbreaker_reason": dealbreaker_reason,
            "drive_resume_link": drive_resume_link,
            "drive_cover_letter_link": drive_cover_letter_link,
            "job_url": job_url,
            "status_history": status_history,
            "updated_at": now_str,
            "created_at": existing_data.get("created_at", now_str) if existing_doc.exists else now_str
        }

        doc_ref.set(payload, merge=True)
        print(f"💾 Firestore Application Record Saved: {company} - {title} [{status}]")
        return doc_id

    def update_application_status(
            self,
            company: str,
            title: str,
            new_status: ApplicationStatus,
            note: Optional[str] = None
    ) -> bool:
        """Advances an application's lifecycle state manually or via callback (e.g. APPLIED -> OA -> INTERVIEW)."""
        doc_id = self._generate_job_id(company, title)
        doc_ref = self.app_collection.document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            print(f"⚠️ Cannot update status: Document {doc_id} ({company} - {title}) not found.")
            return False

        now_str = datetime.utcnow().isoformat()
        data = doc.to_dict()
        status_history = data.get("status_history", [])

        status_history.append({
            "status": new_status,
            "timestamp": now_str,
            "note": note or f"Status transitioned to {new_status}"
        })

        doc_ref.update({
            "status": new_status,
            "status_history": status_history,
            "updated_at": now_str
        })

        print(f"🔄 Application Status Updated: {company} - {title} ➔ {new_status}")
        return True

    # --------------------------------------------------------------------------
    # HIRING MANAGER LEAD PERSISTENCE
    # --------------------------------------------------------------------------
    def is_hiring_lead_processed(self, post_url: str, manager_name: str = "", company: str = "") -> bool:
        doc_id = self._generate_lead_id(post_url, manager_name, company)
        return self.lead_collection.document(doc_id).get().exists

    def save_hiring_lead_record(
            self,
            manager_name: str,
            manager_title: str,
            company: str,
            post_url: str,
            post_text: str,
            drafted_dm: str
    ) -> str:
        doc_id = self._generate_lead_id(post_url, manager_name, company)

        payload = {
            "lead_id": doc_id,
            "manager_name": manager_name,
            "manager_title": manager_title,
            "company": company,
            "post_url": post_url,
            "post_text": post_text[:1000],
            "drafted_dm": drafted_dm,
            "outreach_status": "DRAFTED",  # DRAFTED, SENT, REPLIED
            "updated_at": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }

        self.lead_collection.document(doc_id).set(payload, merge=True)
        print(f"💾 Saved Hiring Manager Lead: {manager_name} ({company})")
        return doc_id

    # --------------------------------------------------------------------------
    # DAILY BRIEFING QUERY
    # --------------------------------------------------------------------------
    def get_daily_standup_summary(self) -> dict:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")

        app_docs = self.app_collection.stream()
        daily_apps = []
        for doc in app_docs:
            data = doc.to_dict()
            if data.get("created_at", "").startswith(today_str):
                daily_apps.append(data)
        daily_apps = sorted(daily_apps, key=lambda x: x.get("match_score", 0), reverse=True)

        lead_docs = self.lead_collection.stream()
        daily_leads = []
        for doc in lead_docs:
            data = doc.to_dict()
            if data.get("created_at", "").startswith(today_str):
                daily_leads.append(data)

        return {
            "job_applications": daily_apps,
            "hiring_leads": daily_leads
        }