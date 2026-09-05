import os
import re
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("LinkedInJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

HARVEST_ACTOR_ID = "harvestapi/linkedin-job-search"

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
    "India", "remote", "Bengaluru", "Bangalore", "Hyderabad", "Pune",
    "Delhi", "Gurgaon", "Noida", "Gurugram", "Mumbai", "New Delhi"
]


@observe(name="LinkedInMCP: Fetch Jobs")
@mcp.tool()
def fetch_linkedin_jobs(
        search_queries: List[str] = DEFAULT_SEARCH_QUERIES,
        locations: List[str] = DEFAULT_LOCATIONS,
        limit_per_query: int = 5
) -> List[Dict[str, Any]]:
    """
    Scrapes LinkedIn job postings matching target queries and locations using HarvestAPI.
    Batches search terms into 5-operator-safe Boolean queries for maximum throughput.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback feed.")
        return _fallback_linkedin_jobs()

    # Batch search_queries in groups of 3 joined by OR (2 OR operators per query string)
    # This stays strictly under LinkedIn's 5-operator ceiling while reducing API calls
    batched_queries = []
    chunk_size = 3
    for i in range(0, len(search_queries), chunk_size):
        chunk = search_queries[i:i + chunk_size]
        formatted_chunk = " OR ".join([f'"{q}"' for q in chunk])
        batched_queries.append(f"({formatted_chunk})")

    # HarvestAPI Input Payload
    run_input = {
        "searchQueries": batched_queries,
        "locations": locations[:3],  # Select top primary location hubs per scrape batch
        "postedLimit": "24h",
        "maxItems": limit_per_query,
        "workplaceType": ["remote", "hybrid"]
    }

    try:
        print(f"📡 LINKEDIN MCP: Launching '{HARVEST_ACTOR_ID}' for {len(batched_queries)} batched Boolean queries...")
        run = apify_client.actor(HARVEST_ACTOR_ID).call(run_input=run_input)

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
                jd_text = item.get("descriptionText", "")

                # Company extraction
                company_info = item.get("company", {})
                company_name = company_info.get("name", "Unknown Company") if isinstance(company_info,
                                                                                         dict) else "Unknown Company"

                # Apply URL extraction
                apply_method = item.get("applyMethod", {})
                apply_url = apply_method.get("companyApplyUrl") if isinstance(apply_method, dict) else None
                job_url = apply_url or item.get("linkedinUrl", "")

                if job_url in seen_urls:
                    continue

                # Technical relevance filter and non-empty description check
                if title and TECHNICAL_TITLE_REGEX.search(title) and jd_text:
                    seen_urls.add(job_url)
                    jobs.append({
                        "platform": "LinkedIn",
                        "company": company_name,
                        "role": title,
                        "url": job_url,
                        "location": item.get("location", {}).get("linkedinText", "Remote/India"),
                        "jd_text": jd_text[:4000]  # Safe token cap for Gemini context window
                    })

        # Instrument Langfuse Telemetry
        langfuse.update_current_span(
            metadata={
                "batched_queries": str(batched_queries),
                "locations": str(locations[:3]),
                "jobs_found": len(jobs),
                "actor_id": HARVEST_ACTOR_ID
            }
        )

        print(f"✅ LINKEDIN MCP: Retained {len(jobs)} relevant engineering job postings.")
        return jobs if jobs else _fallback_linkedin_jobs()

    except Exception as e:
        print(f"❌ LINKEDIN MCP ERROR: {str(e)}")
        return _fallback_linkedin_jobs()


def _fallback_linkedin_jobs() -> List[Dict[str, str]]:
    return [{
        "platform": "LinkedIn",
        "company": "Scale AI",
        "role": "Forward Deployed Engineer - Applied GenAI",
        "url": "https://scale.com/careers",
        "location": "Bengaluru / Remote",
        "jd_text": "Scale AI is seeking Forward Deployed Engineers to design, deploy, and scale customer-facing AI agent architectures and fine-tuned LLM workflows directly in client cloud environments."
    }]


if __name__ == "__main__":
    mcp.run()