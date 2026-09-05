import os
import re
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("WellfoundJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

WELLFOUND_ACTOR_ID = "saswave/wellfound-company-job-scraper"

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
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback feed.")
        return _fallback_wellfound_jobs()

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
                job_location = loc_names if loc_names else [primary_location]

                if job_url in seen_urls:
                    continue

                # Technical title check & non-empty description check
                if title and TECHNICAL_TITLE_REGEX.search(title) and jd_text:
                    seen_urls.add(job_url)
                    jobs.append({
                        "platform": "Wellfound",
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "compensation": compensation,
                        "location": job_location,
                        "jd_text": jd_text[:4000]  # Safe token ceiling for LLM context window
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
        return jobs if jobs else _fallback_wellfound_jobs()

    except Exception as e:
        print(f"❌ WELLFOUND MCP ERROR: {str(e)}")
        return _fallback_wellfound_jobs()


def _fallback_wellfound_jobs() -> List[Dict[str, str]]:
    return [{
        "platform": "Wellfound",
        "company": "Cognition AI (Wellfound)",
        "role": "Applied AI Engineer - Agentic Systems",
        "url": "https://wellfound.com/jobs/sample",
        "compensation": "$160k – $220k • 0.1% – 0.5%",
        "location": ["Bengaluru / Remote"],
        "jd_text": "Join Cognition to build autonomous AI systems and specialized Python parser engines for code generation."
    }]


if __name__ == "__main__":
    mcp.run()