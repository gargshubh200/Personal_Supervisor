import os
import re
import requests
from typing import Dict, Any, List
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("ATSDirectServer")

TECHNICAL_TITLE_REGEX = re.compile(
    r'\b(engineer|developer|software|ai|applied ai|forward deployed|platform|backend|systems|architect)\b',
    re.IGNORECASE
)

# Target high-growth tech companies and their ATS vendor type
TARGET_COMPANIES = [
    {"name": "Stripe", "ats": "greenhouse", "slug": "stripe"},
    {"name": "Palantir", "ats": "lever", "slug": "palantir"},
    {"name": "Scale AI", "ats": "greenhouse", "slug": "scaleai"},
    {"name": "Ramp", "ats": "ashb y", "slug": "ramp"}
]

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
                    if TECHNICAL_TITLE_REGEX.search(title):
                        jobs.append({
                            "platform": f"Direct ATS ({comp['name']})",
                            "company": comp["name"],
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
                    if TECHNICAL_TITLE_REGEX.search(title):
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