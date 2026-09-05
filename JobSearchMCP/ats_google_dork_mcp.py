"""
JobSearchMCP/ats_google_dork_mcp.py

Combines Serper (Google Dorking) for fast ATS URL discovery with Tavily (/extract)
for full-text, clean Markdown job description parsing.
"""

import os
import re
import requests
from typing import Dict, Any, List
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("ATSGoogleDorkServer")
langfuse = get_client()

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Exclude titles outside the candidate's experience tier and technical domain
EXCLUDED_TITLE_REGEX = re.compile(
    r'\b(intern|co-op|graduate|principal|staff|director|vp|vice president|head of|lead manager|account executive|sales|recruiter)\b',
    re.IGNORECASE
)

# Include core target technical domains
TARGET_TITLE_REGEX = re.compile(
    r'\b(forward deployed|applied ai|ai systems|software engineer|systems engineer|platform engineer|backend)\b',
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
    Rejects roles strictly requiring > 3 years or internships (0 years).
    """
    yoe_matches = re.findall(r'(\d+)\+?\s*(?:-\s*\d+)?\s*years?\s+(?:of\s+)?experience', jd_text, re.IGNORECASE)

    if yoe_matches:
        min_years = min(int(y) for y in yoe_matches)
        if min_years > 3 or min_years == 0:
            return False
    return True


def extract_company_from_url(url: str) -> str:
    """Extracts a clean company name from standard ATS URL paths."""
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


@observe(name="ATS Google Dork MCP: Discover & Extract Jobs")
@mcp.tool()
def search_ats_via_google_dork(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Discovers ATS postings via Serper Google Dorking and extracts full-text JDs using Tavily.
    """
    if not SERPER_API_KEY:
        print("⚠️ SERPER_API_KEY not found in .env. Skipping Google Dork search.")
        return []

    dork_query = (
        'site:job-boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com '
        '("Forward Deployed Engineer" OR "Applied AI Engineer" OR "Software Engineer") '
        '("Bengaluru" OR "India" OR "Remote") -Director -VP -Intern'
    )

    url = "https://google.serper.dev/search"
    payload = {"q": dork_query, "num": limit * 3}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    try:
        print(f"📡 ATS DORK MCP: Running Google Dork search via Serper...")
        serper_res = requests.post(url, json=payload, headers=headers, timeout=10)
        serper_data = serper_res.json()
        organic_results = serper_data.get("organic", [])

        candidate_jobs = []
        urls_to_extract = []

        for item in organic_results:
            title = item.get("title", "").split("-")[0].strip()
            link = item.get("link", "")
            snippet = item.get("snippet", "")

            if passes_tier1_title_filter(title) and link:
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
                "serper_results_count": len(organic_results),
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