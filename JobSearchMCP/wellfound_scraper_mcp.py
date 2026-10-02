import os
import re
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

from config_loader import get_master_search_queries, get_default_locations, get_max_jd_chars
from JobSearchMCP.job_filters import passes_yoe_prefilter

load_dotenv()

MAX_JD_CHARS = get_max_jd_chars()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("WellfoundJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

WELLFOUND_ACTOR_ID = "saswave/wellfound-company-job-scraper"

# ── TITLE FILTERS ─────────────────────────────────────────────────────────────
# Tightened per Agent Optimization Context: split broad TECHNICAL_TITLE_REGEX
# into TARGET/EXCLUDED regexes for precision, plus a sourcing-stage YOE guard.

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


@observe(name="WellfoundMCP: Fetch Startup Jobs")
@mcp.tool()
def fetch_wellfound_jobs(
    search_queries: List[str] = DEFAULT_SEARCH_QUERIES,
    locations: List[str] = DEFAULT_LOCATIONS,
    limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Scrapes high-growth startup listings on Wellfound (AngelList) using saswave/wellfound-company-job-scraper.
    Constructs role and location search URLs to fetch listings and extracts full descriptions, compensation, and metadata.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning no jobs.")
        return []

    # Build target search URLs for top 3 search queries
    top_queries = search_queries[:3]
    primary_location = locations[0] if locations else "India"
    loc_slug = re.sub(r'[^a-z0-9]+', '-', primary_location.lower()).strip('-')

    search_urls = []
    for role in top_queries:
        role_slug = re.sub(r'[^a-z0-9]+', '-', role.lower()).strip('-')
        search_urls.append(f"https://wellfound.com/role/l/{role_slug}/{loc_slug}")

    # Payload matching saswave/wellfound-company-job-scraper schema
    run_input = {
        "search_jobs": search_urls,
        "search_companies": [],
        "jobs": []
    }

    try:
        print(f"📡 WELLFOUND MCP: Launching '{WELLFOUND_ACTOR_ID}' for search URLs: {search_urls}...")
        run = apify_client.actor(WELLFOUND_ACTOR_ID).call(run_input=run_input)

        # Safely extract dataset ID
        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", getattr(run, "defaultDatasetId", None))

        jobs = []
        seen_urls = set()

        if dataset_id:
            for item in apify_client.dataset(dataset_id).iterate_items():
                title = item.get("title", "").strip() or item.get("primaryRoleTitle", "").strip()
                jd_text = item.get("description", "")

                # Extract company name from object
                company_data = item.get("company", {})
                if isinstance(company_data, dict):
                    company_name = company_data.get("name", "Startup")
                else:
                    company_name = str(company_data or "Startup")

                job_url = item.get("url") or item.get("job_url", "")
                compensation = item.get("compensation", "")

                # Location extraction
                loc_names = item.get("locationNames", [])
                if not loc_names and item.get("acceptedRemoteLocationNames"):
                    loc_names = item.get("acceptedRemoteLocationNames")
                job_location = ", ".join(loc_names) if loc_names else primary_location

                if job_url in seen_urls:
                    continue

                # Technical title, YOE, and non-empty description filters
                if title and passes_title_filter(title) and jd_text and passes_yoe_filter(jd_text):
                    seen_urls.add(job_url)
                    jobs.append({
                        "platform": "Wellfound",
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "compensation": compensation,
                        "location": job_location,
                        "jd_text": jd_text[:MAX_JD_CHARS]  # Safe token ceiling for LLM context window
                    })

                    if len(jobs) >= limit:
                        break

        # Instrument Telemetry
        langfuse.update_current_span(
            metadata={
                "search_urls": str(search_urls),
                "locations": str(locations[:3]),
                "jobs_found": len(jobs),
                "actor_id": WELLFOUND_ACTOR_ID
            }
        )

        print(f"✅ WELLFOUND MCP: Retained {len(jobs)} relevant startup job postings.")
        return jobs

    except Exception as e:
        print(f"❌ WELLFOUND MCP ERROR: {str(e)}")
        return []



if __name__ == "__main__":
    mcp.run()