import os
import re
from typing import Dict, Any, List
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

from config_loader import get_master_search_queries, get_default_locations

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("LinkedInJobScraperServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

HARVEST_ACTOR_ID = "harvestapi/linkedin-job-search"

# ── TITLE FILTERS ─────────────────────────────────────────────────────────────
# Tightened per Agent Optimization Context: removed generic 'developer'/'architect'
# (too many false positives), split into TARGET/EXCLUDED regexes for precision.

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

# Reject roles that explicitly require 5+ years at the sourcing stage
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


# Search queries/locations are sourced from config.yaml — see indeed_scraper_mcp.py
# comment for rationale (kept as fallback defaults; supervisor_agent.py overrides).
DEFAULT_SEARCH_QUERIES = get_master_search_queries()

DEFAULT_LOCATIONS = get_default_locations()


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

    # Batch search_queries in groups of 3 joined by OR (2 OR operators per query string).
    # Confirmed via HarvestAPI docs: LinkedIn boolean search caps each query string at
    # 5 boolean operators (AND/OR/NOT) and 500 characters — this batching stays well
    # within that ceiling while minimizing the number of actor calls.
    batched_queries = []
    chunk_size = 3
    for i in range(0, len(search_queries), chunk_size):
        chunk = search_queries[i:i + chunk_size]
        formatted_chunk = " OR ".join([f'"{q}"' for q in chunk])
        candidate_query = f"({formatted_chunk})"

        op_count = len(re.findall(r'\b(AND|OR|NOT)\b', candidate_query))
        if op_count > 5 or len(candidate_query) > 500:
            print(f"⚠️  LinkedIn MCP: Skipping oversized batch query ({op_count} ops, {len(candidate_query)} chars).")
            continue

        batched_queries.append(candidate_query)

    if not batched_queries:
        print("⚠️ LinkedIn MCP: No valid batched queries constructed. Returning fallback feed.")
        return _fallback_linkedin_jobs()

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

                # Technical relevance, YOE, and non-empty description filters
                if title and passes_title_filter(title) and jd_text and passes_yoe_filter(jd_text):
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