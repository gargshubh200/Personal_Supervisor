"""
JobSearchMCP/ats_direct_mcp.py

Fetches live engineering job postings directly from public ATS APIs (Greenhouse, Lever, Ashby)
for specific high-growth target companies with zero proxy costs and fast execution times.
"""

import os
import re
import html
import requests
from typing import Dict, Any, List
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

# Initialize MCPServer and Langfuse client
mcp = MCPServer("ATSDirectServer")
langfuse = get_client()

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

# Target high-growth tech companies and their ATS vendor configuration
TARGET_COMPANIES = [
    {"name": "Stripe", "ats": "greenhouse", "slug": "stripe"},
    {"name": "Palantir", "ats": "lever", "slug": "palantir"},
    {"name": "Scale AI", "ats": "greenhouse", "slug": "scaleai"},
    {"name": "Ramp", "ats": "ashby", "slug": "ramp"}
]


def clean_html(raw_html: str) -> str:
    """Strips HTML tags, unescapes HTML entities, and normalizes whitespace."""
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


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


@observe(name="ATS Direct MCP: Fetch Public API Jobs")
@mcp.tool()
def fetch_ats_direct_jobs(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Fetches live engineering jobs directly from company public ATS APIs (Greenhouse, Lever, Ashby)
    with zero proxy costs and fast execution times.
    """
    jobs = []

    for comp in TARGET_COMPANIES:
        try:
            # 1. GREENHOUSE ATS API
            if comp["ats"] == "greenhouse":
                url = f"https://boards-api.greenhouse.io/v1/boards/{comp['slug']}/jobs?content=true"
                res = requests.get(url, timeout=8).json()
                for item in res.get("jobs", []):
                    title = item.get("title", "").strip()
                    raw_content = item.get("content", "")
                    clean_jd = clean_html(raw_content)

                    if passes_tier1_title_filter(title) and passes_tier2_yoe_filter(clean_jd):
                        jobs.append({
                            "platform": f"Direct ATS ({comp['name']})",
                            "company": comp['name'],
                            "role": title,
                            "url": item.get("absolute_url", ""),
                            "location": item.get("location", {}).get("name", "Remote"),
                            "jd_text": clean_jd[:4000]
                        })

            # 2. LEVER ATS API
            elif comp["ats"] == "lever":
                url = f"https://api.lever.co/v0/postings/{comp['slug']}?mode=json"
                res = requests.get(url, timeout=8).json()
                for item in res if isinstance(res, list) else []:
                    title = item.get("text", "").strip()
                    raw_content = item.get("descriptionPlain", "") or item.get("description", "")
                    clean_jd = clean_html(raw_content)

                    if passes_tier1_title_filter(title) and passes_tier2_yoe_filter(clean_jd):
                        jobs.append({
                            "platform": f"Direct ATS ({comp['name']})",
                            "company": comp["name"],
                            "role": title,
                            "url": item.get("hostedUrl", ""),
                            "location": item.get("categories", {}).get("location", "Remote"),
                            "jd_text": clean_jd[:4000]
                        })

            # 3. ASHBY ATS API
            elif comp["ats"] == "ashby":
                url = f"https://api.ashbyhq.com/posting-api/job-board/{comp['slug']}"
                res = requests.get(url, timeout=8).json()
                for item in res.get("jobs", []):
                    title = item.get("title", "").strip()
                    raw_content = item.get("descriptionPlain", "") or item.get("descriptionHtml", "")
                    clean_jd = clean_html(raw_content)

                    if passes_tier1_title_filter(title) and passes_tier2_yoe_filter(clean_jd):
                        location_name = item.get("locationName") or item.get("location", "Remote")
                        jobs.append({
                            "platform": f"Direct ATS ({comp['name']})",
                            "company": comp["name"],
                            "role": title,
                            "url": item.get("jobUrl") or item.get("applyUrl", ""),
                            "location": location_name,
                            "jd_text": clean_jd[:4000]
                        })

            if len(jobs) >= limit:
                break

        except Exception as e:
            print(f"⚠️ Direct ATS API fetch failed for {comp['name']}: {str(e)}")

    # Instrument Telemetry
    langfuse.update_current_span(
        metadata={
            "target_companies": str([c["name"] for c in TARGET_COMPANIES]),
            "jobs_found": len(jobs)
        }
    )

    print(f"✅ DIRECT ATS MCP: Retained {len(jobs)} relevant engineering job postings.")
    return jobs


if __name__ == "__main__":
    mcp.run()