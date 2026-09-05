import os
import requests
from typing import Dict, Any, List
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("ATSGoogleDorkServer")

SERPER_API_KEY = os.getenv("SERPER_API_KEY")

@mcp.tool()
def search_ats_via_google_dork(limit: int = 5) -> List[Dict[str, Any]]:
    """
    Discovers live jobs on Greenhouse, Lever, and Ashby across all companies
    matching targeted role and seniority criteria using Google Search APIs.
    """
    if not SERPER_API_KEY:
        print("⚠️ SERPER_API_KEY not set in .env")
        return []

    dork_query = (
        'site:job-boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com '
        '("Forward Deployed Engineer" OR "Applied AI Engineer" OR "Software Engineer") '
        '("Bengaluru" OR "India" OR "Remote") -Director -VP -Intern'
    )

    url = "https://google.serper.dev/search"
    payload = {"q": dork_query, "num": limit * 2}
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        results = response.json().get("organic", [])

        jobs = []
        for item in results:
            title = item.get("title", "")
            link = item.get("link", "")
            snippet = item.get("snippet", "")

            # Extract company name from URL structure (e.g. job-boards.greenhouse.io/{company}/jobs/{id})
            company_slug = "Tech Company"
            if "greenhouse.io/" in link:
                parts = link.split("greenhouse.io/")[1].split("/")
                company_slug = parts[0].replace("-", " ").title()
            elif "lever.co/" in link:
                parts = link.split("lever.co/")[1].split("/")
                company_slug = parts[0].replace("-", " ").title()

            jobs.append({
                "platform": "Direct ATS Search",
                "company": company_slug,
                "role": title.split("-")[0].strip(),
                "url": link,
                "jd_text": snippet
            })

            if len(jobs) >= limit:
                break

        return jobs

    except Exception as e:
        print(f"❌ Serper ATS Search Error: {str(e)}")
        return []