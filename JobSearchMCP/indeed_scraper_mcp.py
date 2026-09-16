import os
import re
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

from config_loader import get_master_search_queries, get_default_locations, get_india_location_keywords

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("IndeedJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

INDEED_ACTOR_ID = "kaix/indeed-scraper"

# ── TITLE FILTERS ─────────────────────────────────────────────────────────────
# Tightened per Agent Optimization Context. Indeed is deprioritized to a monthly
# breadth scan (see MCP_TOGGLES in supervisor_agent.py) but filters are kept
# aligned with the other MCPs so any retained results stay high-signal.

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


# Search queries/locations are sourced from config.yaml (search.master_search_queries /
# search.default_locations / search.india_location_keywords) — edit that file to tune,
# not this module. supervisor_agent.py always overrides these defaults with
# MASTER_SEARCH_QUERIES/MASTER_LOCATIONS anyway; kept here as sane fallbacks for
# standalone/manual invocation of this MCP.
DEFAULT_SEARCH_QUERIES = get_master_search_queries()
DEFAULT_LOCATIONS = get_default_locations()
INDIA_LOCATION_KEYWORDS = set(get_india_location_keywords())


@observe(name="IndeedMCP: Fetch Jobs")
@mcp.tool()
def fetch_indeed_jobs(
        search_queries: List[str] = DEFAULT_SEARCH_QUERIES,
        locations: List[str] = DEFAULT_LOCATIONS,
        limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Scrapes Indeed job listings using kaix/indeed-scraper actor.
    Combines queries into search expressions and automatically maps 2-letter country codes.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback feed.")
        return _fallback_indeed_jobs()

    # Determine 2-letter country code site ("IN" vs "US")
    has_india_location = any(loc.lower() in INDIA_LOCATION_KEYWORDS for loc in locations)
    country_code = "IN" if has_india_location else "US"
    primary_location = locations[0] if locations else "India"

    # Build title expression string: title:("Role1" or "Role2" or "Role3")
    top_queries = search_queries[:3]
    roles_formatted = " or ".join([f'"{q}"' for q in top_queries])
    keyword_expr = f"title:({roles_formatted})"

    # Payload matching kaix/indeed-scraper schema
    run_input = {
        "keyword": keyword_expr,
        "location": primary_location,
        "country": country_code,
        "maxItems": limit,
        "sort": "date",
        "fromDays": "7",
        "searchMode": "rich"
    }

    try:
        print(f"📡 INDEED MCP: Launching '{INDEED_ACTOR_ID}' in [{country_code}] for expression [{keyword_expr}]...")
        run = apify_client.actor(INDEED_ACTOR_ID).call(run_input=run_input)

        # Extract dataset ID safely
        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", getattr(run, "defaultDatasetId", None))

        jobs = []
        seen_urls = set()

        if dataset_id:
            for item in apify_client.dataset(dataset_id).iterate_items():
                # Title extraction (handles string or object)
                title_val = item.get("title")
                if isinstance(title_val, dict):
                    title = title_val.get("text", "").strip()
                else:
                    title = str(title_val or "").strip()

                # Description extraction (handles string or object)
                desc_val = item.get("description")
                if isinstance(desc_val, dict):
                    jd_text = desc_val.get("text", "") or desc_val.get("html", "")
                else:
                    jd_text = str(desc_val or "")

                # Company extraction
                company_val = item.get("company")
                if isinstance(company_val, dict):
                    company_name = company_val.get("name") or "Unknown Company"
                else:
                    company_name = str(company_val or "Unknown Company")

                # URL extraction (handles nested urls object or direct apply)
                urls_val = item.get("urls", {})
                apply_val = item.get("apply", {})
                job_url = None

                if isinstance(urls_val, dict):
                    job_url = urls_val.get("apply") or urls_val.get("indeed") or urls_val.get("external")

                if not job_url and isinstance(apply_val, dict):
                    job_url = apply_val.get("url")

                if not job_url:
                    job_key = item.get("id", "")
                    domain = "www.indeed.co.in" if country_code == "IN" else "www.indeed.com"
                    job_url = item.get("url") or f"https://{domain}/viewjob?jk={job_key}"

                if job_url in seen_urls:
                    continue

                # Salary extraction
                salary_val = item.get("salary", {})
                formatted_salary = ""
                if isinstance(salary_val, dict):
                    if salary_val.get("text"):
                        formatted_salary = salary_val.get("text")
                    elif salary_val.get("min") is not None:
                        s_min = salary_val.get("min")
                        s_max = salary_val.get("max", "")
                        s_curr = salary_val.get("currency", "INR" if country_code == "IN" else "USD")
                        s_period = salary_val.get("period", "")
                        formatted_salary = f"{s_curr} {s_min}-{s_max} / {s_period}".strip()

                # Location extraction
                loc_val = item.get("location", {})
                if isinstance(loc_val, dict):
                    item_location = loc_val.get("formatted") or loc_val.get("formattedShort") or primary_location
                else:
                    item_location = str(loc_val or primary_location)

                # Technical title, YOE, and non-empty JD filters
                if title and passes_title_filter(title) and jd_text and passes_yoe_filter(jd_text):
                    seen_urls.add(job_url)
                    jobs.append({
                        "platform": "Indeed",
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "salary": formatted_salary,
                        "location": item_location,
                        "jd_text": jd_text[:4000]
                    })

        # Telemetry
        langfuse.update_current_span(
            metadata={
                "keyword_expr": keyword_expr,
                "location": primary_location,
                "country": country_code,
                "jobs_found": len(jobs),
                "actor_id": INDEED_ACTOR_ID
            }
        )

        print(f"✅ INDEED MCP: Retained {len(jobs)} relevant engineering job postings.")
        return jobs if jobs else _fallback_indeed_jobs()

    except Exception as e:
        print(f"❌ INDEED MCP ERROR: {str(e)}")
        return _fallback_indeed_jobs()


def _fallback_indeed_jobs() -> List[Dict[str, str]]:
    return [{
        "platform": "Indeed",
        "company": "Palantir (Indeed)",
        "role": "Forward Deployed Systems Engineer",
        "url": "https://www.indeed.com/viewjob?jk=sample",
        "salary": "INR 15,000,000 - 25,000,000 / YEAR",
        "location": "Bengaluru / Remote",
        "jd_text": "Palantir is seeking Forward Deployed Engineers to bridge customer platform systems and production code bases."
    }]


if __name__ == "__main__":
    mcp.run()