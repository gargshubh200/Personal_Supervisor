"""
Relevant for a few Target Companies with public ATS APIs (e.g., Greenhouse, Lever) that allow direct job fetching without proxy costs.
This MCP tool fetches live engineering job postings, filters them based on candidate experience tier and technical
"""
import os
import re
import requests
from typing import Dict, Any, List
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("ATSDirectServer")

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

# Target high-growth tech companies and their ATS vendor type
TARGET_COMPANIES = [
    {"name": "Stripe", "ats": "greenhouse", "slug": "stripe"},
    {"name": "Palantir", "ats": "lever", "slug": "palantir"},
    {"name": "Scale AI", "ats": "greenhouse", "slug": "scaleai"},
    {"name": "Ramp", "ats": "ashb y", "slug": "ramp"}
]

def passes_tier1_title_filter(title: str) -> bool:
    if EXCLUDED_TITLE_REGEX.search(title):
        return False
    return bool(TARGET_TITLE_REGEX.search(title))


def passes_tier2_yoe_filter(jd_text: str, candidate_yoe: float = 2.5) -> bool:
    # Match patterns like "5+ years", "3-5 years", "minimum 6 years of experience"
    yoe_matches = re.findall(r'(\d+)\+?\s*(?:-\s*\d+)?\s*years?\s+(?:of\s+)?experience', jd_text, re.IGNORECASE)

    if yoe_matches:
        min_years = min(int(y) for y in yoe_matches)
        # Reject if the job strictly requires more than 3 years or 0 years (internship)
        if min_years > 3 or min_years == 0:
            return False
    return True

@mcp.tool()
def fetch_ats_direct_jobs(limit: int = 5) -> List[Dict[str, Any]]:
    """Fetches live engineering jobs directly from company public ATS APIs with zero proxy costs."""
    jobs = []

    for comp in TARGET_COMPANIES:
        try:
            if comp["ats"] == "greenhouse":
                url = f"https://boards-api.greenhouse.io/v1/boards/{comp['slug']}/jobs?content=true"
                res = requests.get(url, timeout=5).json()
                for item in res.get("jobs", []):
                    title = item.get("title", "")
                    if passes_tier1_title_filter(title) and passes_tier2_yoe_filter(item.get("content", "")):
                        jobs.append({
                            "platform": f"Direct ATS ({comp['name']})",
                            "company": item.get('company_name', comp['name']),
                            "role": title,
                            "url": item.get("absolute_url"),
                            "location": item.get("location", {}).get("name", "Remote"),
                            "jd_text": item.get("content", "")[:4000]
                        })

            elif comp["ats"] == "lever":
                url = f"https://api.lever.co/v0/postings/{comp['slug']}?mode=json"
                res = requests.get(url, timeout=5).json()
                for item in res if isinstance(res, list) else []:
                    title = item.get("text", "")
                    if passes_tier1_title_filter(title) and passes_tier2_yoe_filter(item.get("descriptionPlain", "")):
                        jobs.append({
                            "platform": f"Direct ATS ({comp['name']})",
                            "company": comp["name"],
                            "role": title,
                            "url": item.get("hostedUrl"),
                            "location": item.get("categories", {}).get("location", "Remote"),
                            "jd_text": item.get("descriptionPlain", "")[:4000]
                        })

            if len(jobs) >= limit:
                break

        except Exception as e:
            print(f"⚠️ ATS API fetch failed for {comp['name']}: {str(e)}")

    return jobs