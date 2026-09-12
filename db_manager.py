import hashlib
from datetime import datetime
from google.cloud import firestore


class DatabaseManager:
    def __init__(self, collection_name="job_applications"):
        # Auto-authenticates via ADC (Application Default Credentials) in Cloud Run
        self.db = firestore.Client(project="career-os-project")
        self.collection = self.db.collection(collection_name)

    def _generate_job_id(self, company: str, title: str) -> str:
        """Generates a unique deterministic document key based on company and title."""
        raw_str = f"{company.lower().strip()}_{title.lower().strip()}"
        return hashlib.md5(raw_str.encode('utf-8')).hexdigest()

    def is_job_processed(self, company: str, title: str) -> bool:
        """Checks if a job lead was already scraped and analyzed in a prior run."""
        doc_id = self._generate_job_id(company, title)
        doc = self.collection.document(doc_id).get()
        return doc.exists

    def save_application_record(
            self,
            company: str,
            title: str,
            location: str,
            match_score: int,
            strategy: str,
            drive_resume_link: str = None,
            drive_cover_letter_link: str = None,
            hiring_manager_dm: str = None,
            job_url: str = ""
    ) -> str:
        """Persists complete application lifecycle state to Firestore."""
        doc_id = self._generate_generate_id(company, title) if hasattr(self,
                                                                       '_generate_generate_id') else self._generate_job_id(
            company, title)

        payload = {
            "job_id": doc_id,
            "company": company,
            "title": title,
            "location": location,
            "match_score": match_score,
            "strategy": strategy,
            "drive_resume_link": drive_resume_link,
            "drive_cover_letter_link": drive_cover_letter_link,
            "hiring_manager_dm": hiring_manager_dm,
            "job_url": job_url,
            "outreach_status": "PENDING",  # PENDING, SENT, REPLIED
            "updated_at": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }

        self.collection.document(doc_id).set(payload, merge=True)
        print(f" Saved to Firestore DB: {company} - {title} [{strategy}]")
        return doc_id

    def get_daily_standup_summary(self) -> list:
        """Retrieves all application records generated during today's automation pass."""
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        docs = self.collection.stream()

        daily_records = []
        for doc in docs:
            data = doc.to_dict()
            if data.get("created_at", "").startswith(today_str):
                daily_records.append(data)

        # Sort by match score descending
        return sorted(daily_records, key=lambda x: x.get("match_score", 0), reverse=True)