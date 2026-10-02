import os
import re
import json
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

from config_loader import get_master_search_queries, get_default_locations, get_india_location_keywords, get_max_jd_chars
from JobSearchMCP.job_filters import passes_yoe_prefilter

load_dotenv()

MAX_JD_CHARS = get_max_jd_chars()

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


def passes_title_filter(title: str) -> bool:
    """Returns True if title matches target engineering domains and isn't excluded."""
    if not title or EXCLUDED_TITLE_REGEX.search(title):
        return False
    if re.search(r'\bdevops\b', title, re.IGNORECASE) and not re.search(r'\bplatform\b', title, re.IGNORECASE):
        return False
    return bool(TARGET_TITLE_REGEX.search(title))


def passes_yoe_filter(jd_text: str) -> bool:
    """Rejects roles whose stated minimum experience exceeds the candidate's (config.yaml: candidate_eligibility)."""
    return passes_yoe_prefilter(jd_text)


# Search queries/locations are sourced from config.yaml — see indeed_scraper_mcp.py
# comment for rationale (kept as fallback defaults; supervisor_agent.py overrides).
DEFAULT_SEARCH_QUERIES = get_master_search_queries()

DEFAULT_LOCATIONS = get_default_locations()

INDIA_LOCATION_KEYWORDS = set(get_india_location_keywords())


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
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning no jobs.")
        return []

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
                        "jd_text": jd_text[:MAX_JD_CHARS]
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
        return jobs

    except Exception as e:
        print(f"❌ GLASSDOOR MCP ERROR: {str(e)}")
        return []



if __name__ == "__main__":
    mcp.run()