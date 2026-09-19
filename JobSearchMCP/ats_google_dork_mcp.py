"""
JobSearchMCP/ats_google_dork_mcp.py

Combines Serper (Google Dorking) for fast ATS URL discovery with Tavily (/extract)
for full-text, clean Markdown job description parsing.

── ARCHITECTURE NOTE: why this MCP also covers "no public ATS API" companies ──
Some remote-first companies worth tracking (see config.yaml's
`no_public_ats_companies`) don't run Greenhouse/Lever/Ashby/Workable — they're
on Workday, BambooHR, Teamtailor, or a fully custom in-house careers portal.
None of those expose a free, stable, unauthenticated JSON API the way the 4
ATS vendors above do (each Workday tenant has a different URL scheme and often
needs session cookies; BambooHR/Teamtailor postings are rendered client-side).
Building/maintaining a dedicated scraper per platform for a short, fixed list
of ~18 companies — several of which rarely post roles matching this
candidate's target domains — has a poor effort-to-lead ratio and a high
breakage rate (scrapers silently rot whenever any one of these portals
redesigns). Instead, this module opportunistically discovers postings from
those companies via ordinary Google Dorking against their own domain
(`site:{domain}`) — same mechanism as the ATS-domain dorks below, reusing the
exact same Serper + Tavily pipeline and title/YOE filters, at zero additional
maintenance cost. See COMPANY_SCOPED_DORK_QUERIES.
"""

import os
import re
import requests
from typing import Dict, Any, List
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

from config_loader import get_no_public_ats_companies

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("ATSGoogleDorkServer")
langfuse = get_client()

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Exclude titles outside the candidate's experience tier and technical domain
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

# Include core target technical domains. Removed 'forward deployed' (too broad —
# should only be targeted at specific companies via ats_direct_mcp), 'backend' alone
# (too generic, catches unrelated roles), and 'systems engineer' alone (matches
# hardware/embedded/networking roles).
TARGET_TITLE_REGEX = re.compile(
    r'\b('
    r'ai platform|data platform|ai infrastructure|ai backend|'
    r'platform engineer|data infrastructure|'
    r'applied ai|ai systems|'
    r'software engineer|backend engineer'
    r')\b',
    re.IGNORECASE
)


def passes_tier1_title_filter(title: str) -> bool:
    """Verifies that job title matches target engineering domains and excludes non-applicable tiers."""
    if EXCLUDED_TITLE_REGEX.search(title):
        return False
    return bool(TARGET_TITLE_REGEX.search(title))


def passes_tier2_yoe_filter(jd_text: str) -> bool:
    """
    Parses required years of experience.
    Rejects roles strictly requiring 5+ years (per Agent Optimization Context
    EXPERIENCE_YEAR_FILTER max_years_required=4) or internships (0 years).
    """
    yoe_matches = re.findall(r'(\d+)\+?\s*(?:-\s*\d+)?\s*years?\s+(?:of\s+)?experience', jd_text, re.IGNORECASE)

    if yoe_matches:
        min_years = min(int(y) for y in yoe_matches)
        if min_years >= 5 or min_years == 0:
            return False
    return True


NO_PUBLIC_ATS_COMPANIES = get_no_public_ats_companies()
# Domain -> name lookup so company-scoped dork results are labeled correctly
# instead of falling through to the generic "Tech Company" fallback.
DOMAIN_TO_COMPANY_NAME = {c["domain"]: c["name"] for c in NO_PUBLIC_ATS_COMPANIES}


def extract_company_from_url(url: str) -> str:
    """Extracts a clean company name from standard ATS URL paths, or from a
    known no-public-ATS company domain (config.yaml: no_public_ats_companies)."""
    try:
        if "greenhouse.io/" in url:
            parts = url.split("greenhouse.io/")[1].split("/")
            return parts[0].replace("-", " ").title()
        elif "lever.co/" in url:
            parts = url.split("lever.co/")[1].split("/")
            return parts[0].replace("-", " ").title()
        elif "ashbyhq.com/" in url:
            parts = url.split("ashbyhq.com/")[1].split("/")
            return parts[0].replace("-", " ").title()

        for domain, name in DOMAIN_TO_COMPANY_NAME.items():
            if domain in url:
                return name
    except Exception:
        pass
    return "Tech Company"


def fetch_full_jds_via_tavily(urls: List[str]) -> Dict[str, str]:
    """
    Uses Tavily's /extract API endpoint to convert job URLs into clean Markdown text.
    Bypasses JS-heavy SPAs and scrapers without local Playwright/Selenium overhead.
    """
    if not TAVILY_API_KEY or not urls:
        return {}

    try:
        response = requests.post(
            "https://api.tavily.com/extract",
            json={"urls": urls, "api_key": TAVILY_API_KEY},
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            extracted_map = {}
            for res in data.get("results", []):
                extracted_map[res.get("url")] = res.get("raw_content", "") or res.get("content", "")
            return extracted_map
    except Exception as e:
        print(f"⚠️ Tavily Extract Warning: {str(e)}")

    return {}


# ── GOOGLE DORK QUERY STRINGS ─────────────────────────────────────────────────
# Per Agent Optimization Context Task 4: targeted dork patterns per ATS domain,
# combining role keywords with experience-level signals where possible (instead
# of one broad combined query that diluted precision).
DORK_QUERIES = [
    'site:jobs.lever.co "data platform engineer" "python" "2" OR "3" years',
    'site:boards.greenhouse.io "platform engineer" "spark" OR "airflow" India',
    'site:jobs.ashby.com "AI infrastructure" OR "data infrastructure" engineer python',
    'site:greenhouse.io "data engineer" "LLM" OR "agentic" OR "AI" 2 years',
    'site:lever.co "backend engineer" "distributed systems" "python" bengaluru OR remote India',
    'site:boards.greenhouse.io "software engineer" "data platform" "kafka" OR "spark" India',
    'site:jobs.ashbyhq.com "platform engineer" "kubernetes" OR "python" India OR remote',
    'site:job-boards.greenhouse.io "AI platform engineer" OR "applied ai engineer" python',
    'site:jobs.lever.co "AI infrastructure" OR "ai backend" engineer python "2" OR "3" years',
    'site:boards.greenhouse.io "data engineer" "airflow" OR "spark" OR "delta lake" "2" OR "3" years',
]

# ── COMPANY-SCOPED DORK QUERIES (no public ATS API) ───────────────────────────
# Generated from config.yaml's no_public_ats_companies list — one query per
# company, scoped to that company's own domain instead of a shared ATS domain.
# See the architecture note at the top of this file for why these companies
# are covered this way instead of via a dedicated per-platform scraper.
COMPANY_SCOPED_DORK_QUERIES = [
    f'site:{c["domain"]} careers "platform engineer" OR "software engineer" OR '
    f'"backend engineer" OR "data engineer" OR "applied ai"'
    for c in NO_PUBLIC_ATS_COMPANIES
]

DORK_QUERIES = DORK_QUERIES + COMPANY_SCOPED_DORK_QUERIES


@observe(name="ATS Google Dork MCP: Discover & Extract Jobs")
@mcp.tool()
def search_ats_via_google_dork(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Discovers ATS postings via Serper Google Dorking (across DORK_QUERIES, each targeting
    a specific role/ATS-domain/experience-level combination) and extracts full-text JDs via Tavily.

    Args:
        limit: Max jobs to retain overall across all dork queries.
    """
    if not SERPER_API_KEY:
        print("⚠️ SERPER_API_KEY not found in .env. Skipping Google Dork search.")
        return []

    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    candidate_jobs = []
    urls_to_extract = []
    seen_urls = set()
    query_stats = {}

    try:
        for dork_query in DORK_QUERIES:
            if len(candidate_jobs) >= limit:
                break

            payload = {"q": dork_query, "num": 5}

            try:
                print(f"📡 ATS DORK MCP: Querying [{dork_query}]...")
                serper_res = requests.post(url, json=payload, headers=headers, timeout=10)
                organic_results = serper_res.json().get("organic", [])
                query_stats[dork_query] = len(organic_results)

                for item in organic_results:
                    title = item.get("title", "").split("-")[0].strip()
                    link = item.get("link", "")
                    snippet = item.get("snippet", "")

                    if link in seen_urls:
                        continue

                    if passes_tier1_title_filter(title) and link:
                        seen_urls.add(link)
                        company_name = extract_company_from_url(link)
                        candidate_jobs.append({
                            "platform": f"ATS Discovery ({company_name})",
                            "company": company_name,
                            "role": title,
                            "url": link,
                            "snippet": snippet
                        })
                        urls_to_extract.append(link)

                    if len(candidate_jobs) >= limit:
                        break

            except Exception as e:
                print(f"❌ ATS DORK MCP query error [{dork_query}]: {str(e)}")
                query_stats[dork_query] = 0

        # Step 2: Extract full Markdown JDs via Tavily /extract endpoint
        extracted_jds = {}
        if urls_to_extract:
            print(f"🧠 ATS DORK MCP: Extracting full JDs for {len(urls_to_extract)} links via Tavily...")
            extracted_jds = fetch_full_jds_via_tavily(urls_to_extract)

        # Step 3: Combine and apply Tier 2 YOE filter
        final_jobs = []
        for job in candidate_jobs:
            full_text = extracted_jds.get(job["url"]) or job["snippet"]

            if passes_tier2_yoe_filter(full_text):
                final_jobs.append({
                    "platform": job["platform"],
                    "company": job["company"],
                    "role": job["role"],
                    "url": job["url"],
                    "location": "Remote / India",
                    "jd_text": full_text[:4000]
                })

        # Instrument Telemetry
        langfuse.update_current_span(
            metadata={
                "dork_queries_run": len(DORK_QUERIES),
                "query_stats": query_stats,
                "urls_extracted": len(urls_to_extract),
                "retained_jobs": len(final_jobs)
            }
        )

        print(f"✅ ATS DORK MCP: Retained {len(final_jobs)} relevant ATS engineering postings.")
        return final_jobs

    except Exception as e:
        print(f"❌ ATS DORK MCP ERROR: {str(e)}")
        return []


if __name__ == "__main__":
    mcp.run()