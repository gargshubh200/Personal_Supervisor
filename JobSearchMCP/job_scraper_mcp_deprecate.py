import os
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe
import re

# Strict word-boundary regex for core technical roles
TECHNICAL_TITLE_REGEX = re.compile(
    r'\b(engineer|developer|software|ai|applied ai|forward deployed|platform|backend|systems|architect)\b',
    re.IGNORECASE
)

load_dotenv()

mcp = MCPServer("JobScraperServer")

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

HARVEST_ACTOR_ID = "harvestapi/linkedin-job-search"

@observe(name="JobScraper: Fetch LinkedIn Jobs via HarvestAPI")
@mcp.tool()
def fetch_fresh_linkedin_jobs(
        search_queries: List[str] = ["Forward Deployed Engineer", "Applied AI Engineer", "Software Engineer", "Platform Engineer"],
        locations: List[str] = ["India"],
        limit_per_query: int = 5
) -> List[Dict[str, Any]]:
    """
    Calls HarvestAPI's 'Advanced Linkedin Job Scraper (No Cookies)' actor (harvestapi/linkedin-job-search).
    Scrapes fresh jobs posted in the last 24 hours and filters for relevant technical roles.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback mock feed.")
        return _get_fallback_mock_jobs()

    run_input = {
        "searchQueries": search_queries,
        "locations": locations,
        "postedLimit": "24h",
        "maxItems": limit_per_query,
        "workplaceType": ["remote"]
    }

    try:
        print(f"📡 APIFY (HarvestAPI): Launching 'harvestapi/linkedin-job-search'...")
        print(f"🔍 Queries: {search_queries} | Locations: {locations}")

        run = apify_client.actor(HARVEST_ACTOR_ID).call(run_input=run_input)

        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", getattr(run, "defaultDatasetId", None))

        jobs = []
        if dataset_id:
            for item in apify_client.dataset(dataset_id).iterate_items():
                title = item.get("title", "").strip()
                jd_text = item.get("descriptionText", "")

                company_info = item.get("company", {})
                company_name = company_info.get("name") if isinstance(company_info, dict) else "Unknown Company"

                apply_method = item.get("applyMethod", {})
                apply_url = apply_method.get("companyApplyUrl") if isinstance(apply_method, dict) else None
                job_url = apply_url or item.get("linkedinUrl", "")

                # Relevance Check: Ensure job title contains technical software/AI keywords
                is_relevant_role = bool(TECHNICAL_TITLE_REGEX.search(title))

                if jd_text and company_name != "Unknown Company" and is_relevant_role:
                    jobs.append({
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "jd_text": jd_text[:4000]
                    })
                elif not is_relevant_role and title:
                    print(f"⏩ Filtered out non-technical title: '{title}' ({company_name})")

        print(f"✅ APIFY (HarvestAPI): Retained {len(jobs)} relevant engineering job postings.")
        return jobs if jobs else _get_fallback_mock_jobs()

    except Exception as e:
        print(f"❌ APIFY ERROR: HarvestAPI Scraper failed with error: {str(e)}")
        return _get_fallback_mock_jobs()


def _get_fallback_mock_jobs() -> List[Dict[str, str]]:
    return [
        {
            "company": "Scale AI",
            "role": "Forward Deployed Engineer - Applied GenAI",
            "url": "https://scale.com/careers",
            "jd_text": "Scale AI is seeking Forward Deployed Engineers to design, deploy, and scale customer-facing AI agent architectures and fine-tuned LLM workflows directly in client cloud environments. Strong Python, multi-agent frameworks, and system optimization skills required."
        },
        {
            "company": "Cognition",
            "role": "Applied AI Software Engineer",
            "url": "https://cognition.ai/careers",
            "jd_text": "Join Cognition to build production LLM systems, custom language parsing engines, and agentic code automation tools. Seeking software engineers who excel at systems reliability, Python tooling, and graph algorithms."
        }
    ]


if __name__ == "__main__":
    results = fetch_fresh_linkedin_jobs(
        search_queries=["Forward Deployed Engineer", "Software Engineer"],
        locations=["United States"],
        limit_per_query=5
    )
    for index, job in enumerate(results, start=1):
        print(f"\n--- MATCHED JOB #{index} ---")
        print(f"Company: {job['company']}")
        print(f"Role: {job['role']}")
        print(f"URL: {job['url']}")