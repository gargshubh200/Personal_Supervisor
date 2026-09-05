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

TECHNICAL_TITLE_REGEX = re.compile(
    r'\b(engineer|developer|software|ai|applied ai|forward deployed|platform|backend|systems|architect)\b',
    re.IGNORECASE
)

DEFAULT_SEARCH_QUERIES = [
    "Forward Deployed Engineer", "Applied AI Engineer", "Software Engineer",
    "Platform Engineer", "Application Engineer", "AI Engineer",
    "Machine Learning Engineer", "Software Developer", "Python Developer"
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

                if title and TECHNICAL_TITLE_REGEX.search(title) and jd_text:
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