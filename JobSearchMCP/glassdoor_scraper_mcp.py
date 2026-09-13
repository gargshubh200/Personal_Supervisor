import os
import re
import json
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("GlassdoorJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

GLASSDOOR_ACTOR_ID = "blackfalcondata/glassdoor-job-scraper"

# ── TITLE FILTERS ─────────────────────────────────────────────────────────────
# Per Agent Optimization Context, Glassdoor is recommended to be dropped entirely
# (heavy duplication with LinkedIn/Indeed). MCP_TOGGLES disables it by default;
# filters below are kept aligned in case it's ever re-enabled for ad-hoc scans.

EXCLUDED_TITLE_REGEX = re.compile(
    r'\b('
    r'intern|co-op|co op|graduate|new grad|'
    r'principal|staff|distinguished|fellow|'
    r'director|vp|vice president|head of|'
    r'lead manager|account executive|'
    r'sales|recruiter|talent|'
    r'solutions engineer|customer success|pre-sales|presales|'
    r'data scientist|research scientist|research engineer|'
    r'machine learning engineer|ml engineer|'
    r'frontend|front-end|full.?stack|fullstack|'
    r'android|ios|mobile|react|angular|vue'
    r')\b',
    re.IGNORECASE
)

TARGET_TITLE_REGEX = re.compile(
    r'\b('
    r'ai platform|data platform|ai infrastructure|ai backend|'
    r'platform engineer|data infrastructure|'
    r'applied ai|ai systems|'
    r'software engineer|backend engineer'
    r')\b',
    re.IGNORECASE
)

EXPERIENCE_YEAR_REJECT_REGEX = re.compile(
    r'\b(5|6|7|8|9|10)\+?\s*(?:to\s*\d+\s*)?years?\s+(?:of\s+)?'
    r'(?:professional\s+)?(?:software\s+)?(?:work\s+)?experience\b',
    re.IGNORECASE
)


def passes_title_filter(title: str) -> bool:
    """Returns True if title matches target engineering domains and isn't excluded."""
    if not title or EXCLUDED_TITLE_REGEX.search(title):
        return False
    if re.search(r'\bdevops\b', title, re.IGNORECASE) and not re.search(r'\bplatform\b', title, re.IGNORECASE):
        return False
    return bool(TARGET_TITLE_REGEX.search(title))


def passes_yoe_filter(jd_text: str) -> bool:
    """Rejects roles explicitly requiring 5+ years — saves downstream matching-agent tokens."""
    if not jd_text:
        return True
    return not EXPERIENCE_YEAR_REJECT_REGEX.search(jd_text)


# Tightened to remove noisy queries duplicated from LinkedIn/Indeed.
DEFAULT_SEARCH_QUERIES = [
    "Data Platform Engineer", "AI Platform Engineer", "Platform Engineer",
    "Data Infrastructure Engineer", "AI Infrastructure Engineer", "Software Engineer Data Systems"
]

DEFAULT_LOCATIONS = [
    "India", "Remote", "Bengaluru", "Bangalore", "Hyderabad", "Pune",
    "Delhi", "Gurgaon", "Noida", "Gurugram", "Mumbai", "New Delhi"
]

INDIA_LOCATION_KEYWORDS = {
    "india", "bengaluru", "bangalore", "hyderabad", "pune",
    "delhi", "gurgaon", "noida", "gurugram", "mumbai", "new delhi"
}


@observe(name="GlassdoorMCP: Fetch Jobs")
@mcp.tool()
def fetch_glassdoor_jobs(
    search_queries: List[str] = DEFAULT_SEARCH_QUERIES,
    locations: List[str] = DEFAULT_LOCATIONS,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Scrapes Glassdoor job listings using blackfalcondata/glassdoor-job-scraper actor.
    Passes stringified JSON arrays for multi-query and multi-location input parameters.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback feed.")
        return _fallback_glassdoor_jobs()

    has_india_location = any(loc.lower() in INDIA_LOCATION_KEYWORDS for loc in locations)
    country_code = "IN" if has_india_location else "US"

    top_queries = search_queries[:3]
    top_locations = locations[:3]

    # Convert Python lists to stringified JSON arrays to satisfy Apify's string schema validation
    query_payload = json.dumps(top_queries)
    location_payload = json.dumps(top_locations)

    run_input = {
        "query": query_payload,
        "location": location_payload,
        "country": country_code,
        "maxResults": limit,
        "postedDays": 7
    }

    try:
        print(f"📡 GLASSDOOR MCP: Launching '{GLASSDOOR_ACTOR_ID}' in market '{country_code}' for query payload {query_payload}...")
        run = apify_client.actor(GLASSDOOR_ACTOR_ID).call(run_input=run_input)

        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", getattr(run, "defaultDatasetId", None))

        jobs = []
        seen_urls = set()

        if dataset_id:
            for item in apify_client.dataset(dataset_id).iterate_items():
                title = item.get("title", "").strip()
                jd_text = item.get("description", "")
                company_name = item.get("company", "Unknown Company")

                job_url = item.get("applyUrl") or item.get("canonicalUrl") or item.get("sourceUrl") or ""
                salary = item.get("salaryText", "")
                rating = item.get("companyRating")
                item_location = item.get("locationFormatted") or item.get("location") or locations[0]

                if job_url in seen_urls:
                    continue

                if title and passes_title_filter(title) and jd_text and passes_yoe_filter(jd_text):
                    seen_urls.add(job_url)
                    jobs.append({
                        "platform": "Glassdoor",
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "salary": salary,
                        "company_rating": rating,
                        "location": item_location,
                        "jd_text": jd_text[:4000]
                    })

        langfuse.update_current_span(
            metadata={
                "search_queries": query_payload,
                "locations": location_payload,
                "country_code": country_code,
                "jobs_found": len(jobs),
                "actor_id": GLASSDOOR_ACTOR_ID
            }
        )

        print(f"✅ GLASSDOOR MCP: Retained {len(jobs)} relevant engineering job postings.")
        return jobs if jobs else _fallback_glassdoor_jobs()

    except Exception as e:
        print(f"❌ GLASSDOOR MCP ERROR: {str(e)}")
        return _fallback_glassdoor_jobs()


def _fallback_glassdoor_jobs() -> List[Dict[str, str]]:
    return [{
        "platform": "Glassdoor",
        "company": "Databricks (Glassdoor)",
        "role": "Forward Deployed AI Engineer",
        "url": "https://www.glassdoor.com/job-listing/sample",
        "salary": "INR 15,000,000 - 25,000,000 / YEAR",
        "company_rating": 4.5,
        "location": "Bengaluru / Remote",
        "jd_text": "Databricks is hiring Forward Deployed Engineers to deploy agentic workflows on enterprise platform architecture."
    }]


if __name__ == "__main__":
    mcp.run()