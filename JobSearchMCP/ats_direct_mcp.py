"""
JobSearchMCP/ats_direct_mcp.py

Fetches live engineering job postings directly from public ATS APIs (Greenhouse, Lever, Ashby)
for specific high-growth target companies with zero proxy costs and fast execution times.

v2 Changes:
- Per-company limit instead of global limit (prevents early exit after first company)
- Expanded TARGET_COMPANIES with verified ATS slugs (18 companies)
- Tightened TARGET_TITLE_REGEX to reduce false positives
- Expanded EXCLUDED_TITLE_REGEX with missing exclusions
- Added minimum JD length guard for Ashby (prevents empty JD false passes)
- Added EXPERIENCE_YEAR_REJECT_REGEX for 5+ year role detection
"""

import os
import re
import html
import requests
from typing import Dict, Any, List
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config_loader import get_ats_target_companies

load_dotenv()

mcp = MCPServer("ATSDirectServer")
langfuse = get_client()

# ── TITLE FILTERS ─────────────────────────────────────────────────────────────

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

# ── EXPERIENCE YEAR FILTER ────────────────────────────────────────────────────

# Reject roles that explicitly require 5+ years
EXPERIENCE_YEAR_REJECT_REGEX = re.compile(
    r'\b(5|6|7|8|9|10)\+?\s*(?:to\s*\d+\s*)?years?\s+(?:of\s+)?'
    r'(?:professional\s+)?(?:software\s+)?(?:work\s+)?experience\b',
    re.IGNORECASE
)

# ── TARGET COMPANIES ──────────────────────────────────────────────────────────
# ATS slugs verified against public API endpoints.
# Test any new slug: curl https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# If 404 — slug is wrong. Common variations: company-inc, companyai, company-labs

# ── TARGET COMPANIES ──────────────────────────────────────────────────────────
# ATS slugs verified against public API endpoints. Full list lives in
# config.yaml (ats_target_companies) — edit that file to add/remove companies.
# Test any new slug: curl https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
# If 404 — slug is wrong. Common variations: company-inc, companyai, company-labs

TARGET_COMPANIES = get_ats_target_companies()

# ── COMPANY-LEVEL TITLE OVERRIDES ─────────────────────────────────────────────
# Some companies use role titles not caught by TARGET_TITLE_REGEX.
# Add company-specific additional patterns here.
COMPANY_TITLE_OVERRIDES = {
    "Palantir":  re.compile(r'\b(forward deployed|fde|sde|devops|reliability)\b', re.IGNORECASE),
    "Scale AI":  re.compile(r'\b(forward deployed|applied|ai|ml|data)\b', re.IGNORECASE),
    "Glean":     re.compile(r'\b(forward deployed|applied ai|backend)\b', re.IGNORECASE),
}

# Minimum JD length — below this likely means empty/malformed JD, skip
MIN_JD_LENGTH = 100


# ── UTILITY FUNCTIONS ─────────────────────────────────────────────────────────

def clean_html(raw_html: str) -> str:
    """Strips HTML tags, unescapes HTML entities, and normalizes whitespace."""
    if not raw_html:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_html)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def passes_title_filter(title: str, company_name: str = "") -> bool:
    """
    Returns True if title matches target engineering domains
    and does not match excluded tiers/domains.
    Company-level overrides applied for known exceptions.
    """
    if EXCLUDED_TITLE_REGEX.search(title):
        return False

    # Check company-level override first
    override = COMPANY_TITLE_OVERRIDES.get(company_name)
    if override and override.search(title):
        return True

    return bool(TARGET_TITLE_REGEX.search(title))


def passes_yoe_filter(jd_text: str) -> bool:
    """
    Rejects roles explicitly requiring 5+ years.
    Also rejects internships (0 years required).
    Returns True if no explicit year requirement found (safe default).
    """
    if not jd_text or len(jd_text) < MIN_JD_LENGTH:
        return False  # Malformed or empty JD — skip

    # Hard reject: 5+ year requirement
    if EXPERIENCE_YEAR_REJECT_REGEX.search(jd_text):
        return False

    # Reject internships
    yoe_matches = re.findall(
        r'(\d+)\+?\s*(?:-\s*\d+)?\s*years?\s+(?:of\s+)?experience',
        jd_text,
        re.IGNORECASE
    )
    if yoe_matches:
        min_years = min(int(y) for y in yoe_matches)
        if min_years == 0:
            return False

    return True


# A single transient network hiccup (read timeout, connection reset) with any
# one ATS vendor shouldn't permanently drop that company for the whole run —
# retry briefly before giving up and letting the caller's try/except skip it.
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=6),
    retry=retry_if_exception_type((requests.exceptions.Timeout, requests.exceptions.ConnectionError)),
    reraise=True
)
def _get_json_with_retry(url: str, timeout: int = 10):
    """GETs a URL and returns parsed JSON, retrying up to 3x on timeout/connection errors."""
    return requests.get(url, timeout=timeout).json()


def fetch_greenhouse_jobs(comp: Dict, per_company_limit: int) -> List[Dict]:
    """Fetches jobs from Greenhouse ATS API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{comp['slug']}/jobs?content=true"
    res = _get_json_with_retry(url)
    results = []

    for item in res.get("jobs", []):
        if len(results) >= per_company_limit:
            break
        title = item.get("title", "").strip()
        clean_jd = clean_html(item.get("content", ""))

        if passes_title_filter(title, comp["name"]) and passes_yoe_filter(clean_jd):
            results.append({
                "platform": f"Direct ATS ({comp['name']})",
                "company": comp["name"],
                "role": title,
                "url": item.get("absolute_url", ""),
                "location": item.get("location", {}).get("name", "Remote"),
                "jd_text": clean_jd[:4000]
            })

    return results


def fetch_lever_jobs(comp: Dict, per_company_limit: int) -> List[Dict]:
    """Fetches jobs from Lever ATS API."""
    url = f"https://api.lever.co/v0/postings/{comp['slug']}?mode=json"
    res = _get_json_with_retry(url)
    results = []

    for item in (res if isinstance(res, list) else []):
        if len(results) >= per_company_limit:
            break
        title = item.get("text", "").strip()
        raw_content = item.get("descriptionPlain", "") or item.get("description", "")
        clean_jd = clean_html(raw_content)

        if passes_title_filter(title, comp["name"]) and passes_yoe_filter(clean_jd):
            results.append({
                "platform": f"Direct ATS ({comp['name']})",
                "company": comp["name"],
                "role": title,
                "url": item.get("hostedUrl", ""),
                "location": item.get("categories", {}).get("location", "Remote"),
                "jd_text": clean_jd[:4000]
            })

    return results


def fetch_ashby_jobs(comp: Dict, per_company_limit: int) -> List[Dict]:
    """Fetches jobs from Ashby ATS API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{comp['slug']}"
    res = _get_json_with_retry(url)
    results = []

    for item in res.get("jobs", []):
        if len(results) >= per_company_limit:
            break
        title = item.get("title", "").strip()
        # Ashby: try descriptionPlain first, fall back to HTML (clean it)
        raw_content = (
            item.get("descriptionPlain", "") or
            item.get("descriptionHtml", "") or
            item.get("description", "")
        )
        clean_jd = clean_html(raw_content)

        if passes_title_filter(title, comp["name"]) and passes_yoe_filter(clean_jd):
            location_name = (
                item.get("locationName") or
                item.get("location") or
                "Remote"
            )
            results.append({
                "platform": f"Direct ATS ({comp['name']})",
                "company": comp["name"],
                "role": title,
                "url": item.get("jobUrl") or item.get("applyUrl", ""),
                "location": location_name,
                "jd_text": clean_jd[:4000]
            })

    return results

def fetch_workable_jobs(comp: Dict, per_company_limit: int) -> List[Dict]:
    url = f"https://{comp['slug']}.workable.com/api/v3/jobs"
    res = _get_json_with_retry(url)
    results = []

    for item in res.get("jobs", []):
        if len(results) >= per_company_limit:
            break
        title = item.get("title", "").strip()
        # Workable provides full_description in the list endpoint
        clean_jd = item.get("full_description", "") or item.get("description", "")
        clean_jd = clean_html(clean_jd)

        if passes_title_filter(title, comp["name"]) and passes_yoe_filter(clean_jd):
            results.append({
                "platform": f"Direct ATS ({comp['name']})",
                "company": comp["name"],
                "role": title,
                "url": item.get("url", ""),
                "location": item.get("location", {}).get("city", "Remote"),
                "jd_text": clean_jd[:4000]
            })

    return results


# ── MAIN TOOL ─────────────────────────────────────────────────────────────────
ATS_FETCHERS = {
        "greenhouse": fetch_greenhouse_jobs,
        "lever":      fetch_lever_jobs,
        "ashby":      fetch_ashby_jobs,
        "workable":   fetch_workable_jobs
    }

@observe(name="ATS Direct MCP: Fetch Public API Jobs")
@mcp.tool()
def fetch_ats_direct_jobs(per_company_limit: int = 3) -> List[Dict[str, Any]]:
    """
    Fetches live engineering jobs directly from company ATS APIs (Greenhouse, Lever, Ashby, Workable).
    Zero proxy cost. Applies per-company limits to ensure all companies are sampled.

    Args:
        per_company_limit: Max jobs to return per company (default 3, since
                           TARGET_COMPANIES now covers ~40 companies after
                           merging in the former company_specific_mcp.py list).
                           Total max = per_company_limit * len(TARGET_COMPANIES).
    """
    all_jobs = []
    fetch_errors = []

    for comp in TARGET_COMPANIES:
        try:
            fetcher = ATS_FETCHERS.get(comp["ats"])
            if not fetcher:
                print(f"⚠️  Unknown ATS vendor '{comp['ats']}' for {comp['name']} — skipping.")
                continue

            company_jobs = fetcher(comp, per_company_limit)
            all_jobs.extend(company_jobs)
            print(f"✅ {comp['name']} ({comp['ats']}): {len(company_jobs)} jobs retained.")

        except Exception as e:
            error_msg = f"{comp['name']}: {str(e)}"
            fetch_errors.append(error_msg)
            print(f"❌ ATS fetch failed — {error_msg}")

    langfuse.update_current_span(
        metadata={
            "target_companies": [c["name"] for c in TARGET_COMPANIES],
            "total_jobs_found": len(all_jobs),
            "fetch_errors": fetch_errors,
            "per_company_limit": per_company_limit
        }
    )

    print(f"\n📊 ATS DIRECT MCP SUMMARY: {len(all_jobs)} jobs across {len(TARGET_COMPANIES)} companies.")
    if fetch_errors:
        print(f"⚠️  {len(fetch_errors)} companies had errors: {fetch_errors}")

    return all_jobs


if __name__ == "__main__":
    mcp.run()