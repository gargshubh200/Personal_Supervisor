import os
import re
import urllib.parse
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("AmbitionBoxJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

AMBITIONBOX_ACTOR_ID = "yodeling_elevator/ambitionbox-job-scrapper"

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


@observe(name="AmbitionBoxMCP: Fetch India Market Jobs")
@mcp.tool()
def fetch_ambitionbox_jobs(
        search_queries: List[str] = DEFAULT_SEARCH_QUERIES,
        locations: List[str] = DEFAULT_LOCATIONS,
        limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Scrapes AmbitionBox job listings using yodeling_elevator/ambitionbox-job-scrapper actor,
    supporting multi-query target roles and location hubs in the India tech ecosystem.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback feed.")
        return _fallback_ambitionbox_jobs()

    # Determine primary location and build search URLs for top 3 target roles
    primary_location = locations[0] if locations else "Bengaluru"
    top_queries = search_queries[:3]

    start_urls = []
    for role in top_queries:
        encoded_query = urllib.parse.quote_plus(f"{role} {primary_location}".strip())
        start_urls.append(f"https://www.ambitionbox.com/jobs?q={encoded_query}")

    # Input payload matching actor schema
    run_input = {
        "startUrls": start_urls,
        "maxConcurrency": 20,
        "maxRequestsPerMinute": 600,
        "proxyConfiguration": {
            "useApifyProxy": False
        }
    }

    try:
        print(
            f"📡 AMBITIONBOX MCP: Launching '{AMBITIONBOX_ACTOR_ID}' for queries {top_queries} in '{primary_location}'...")
        run = apify_client.actor(AMBITIONBOX_ACTOR_ID).call(run_input=run_input)

        # Safely extract dataset ID whether run is dict or ActorRun object
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
                company_name = item.get("companyName", "Unknown Company")
                job_url = item.get("sourceUrl") or item.get("jobId") or "https://www.ambitionbox.com/jobs"

                if job_url in seen_urls:
                    continue

                # Parse structured salary object (min, max, currency)
                salary_data = item.get("salary", {})
                formatted_salary = ""
                if isinstance(salary_data, dict) and salary_data.get("min"):
                    s_min = salary_data.get("min")
                    s_max = salary_data.get("max", "")
                    s_curr = salary_data.get("currency", "INR")
                    formatted_salary = (
                        f"{s_curr} {s_min:,} - {s_max:,}"
                        if isinstance(s_min, (int, float))
                        else f"{s_curr} {s_min} - {s_max}"
                    )

                # Parse structured experience object (min, max)
                exp_data = item.get("experience", {})
                formatted_exp = ""
                if isinstance(exp_data, dict) and exp_data.get("min") is not None:
                    formatted_exp = f"{exp_data.get('min')}-{exp_data.get('max')} yrs"

                rating = item.get("companyRating")
                job_location = item.get("location") or primary_location

                # Technical title regex check and non-empty description check
                if title and TECHNICAL_TITLE_REGEX.search(title) and jd_text:
                    seen_urls.add(job_url)
                    jobs.append({
                        "platform": "AmbitionBox",
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "salary": formatted_salary,
                        "experience": formatted_exp,
                        "company_rating": rating,
                        "location": job_location,
                        "skills": item.get("skills", []),
                        "jd_text": jd_text[:4000]  # Safe token ceiling for LLM context window
                    })

                    if len(jobs) >= limit:
                        break

        # Instrument Langfuse Telemetry
        langfuse.update_current_span(
            metadata={
                "search_queries": str(top_queries),
                "locations": str(locations[:3]),
                "jobs_found": len(jobs),
                "actor_id": AMBITIONBOX_ACTOR_ID
            }
        )

        print(f"✅ AMBITIONBOX MCP: Retained {len(jobs)} relevant engineering job postings.")
        return jobs if jobs else _fallback_ambitionbox_jobs()

    except Exception as e:
        print(f"❌ AMBITIONBOX MCP ERROR: {str(e)}")
        return _fallback_ambitionbox_jobs()


def _fallback_ambitionbox_jobs() -> List[Dict[str, str]]:
    return [{
        "platform": "AmbitionBox",
        "company": "o9 Solutions (AmbitionBox)",
        "role": "Software Engineer - Applied AI Systems",
        "url": "https://www.ambitionbox.com/jobs",
        "salary": "INR 1,500,000 - 2,500,000",
        "experience": "2-4 yrs",
        "company_rating": 4.2,
        "location": "Bengaluru",
        "skills": ["Python", "FastAPI", "ANTLR", "LangChain"],
        "jd_text": "Build enterprise semantic context generation engines, graph algorithms, and multi-agent frameworks."
    }]


if __name__ == "__main__":
    mcp.run()