"""
JobSearchMCP/job_filters.py

Shared, cheap sourcing-stage filters used by every job-source MCP, so each
source doesn't carry its own (diverging) copy of the same regex.

This is only a PRE-filter to save LLM calls on obvious mismatches. The
authoritative experience / location / employment-type decision is made later
by the LLM-extracted JD analysis + OtherMCP/eligibility_mcp.py.
"""

import re
from typing import Optional

from config_loader import get_candidate_eligibility

SKIP_IF_MIN_YEARS_AT_LEAST = get_candidate_eligibility()["skip_if_min_years_at_least"]

# Matches "3+ years", "3-5 years", "3 – 5+ yrs", "minimum 7 years", "5 years'"
# and captures the LOWER bound. Requiring the word "experience" nearby (after,
# or just before as in "Experience - 5-7 years") keeps out unrelated numbers
# like "founded 10 years ago".
_YEARS_REGEX = re.compile(
    r"(?<![\d.])(\d{1,2})(?:\.\d)?\s*\+?\s*(?:(?:-|–|—|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b",
    re.IGNORECASE
)
_EXPERIENCE_WORD_REGEX = re.compile(r"\bexp(?:erience|\.)?\b", re.IGNORECASE)

# Upper sanity bound — anything larger is almost certainly not an experience requirement.
_MAX_PLAUSIBLE_YEARS = 15


def extract_min_years_required(jd_text: str) -> Optional[int]:
    """
    Returns the strictest (largest) lower bound among experience requirements
    stated in the JD, or None if none are found. Using the largest lower bound
    means "5+ years overall ... 2+ years with Kafka" resolves to 5, not 2.
    """
    if not jd_text:
        return None

    found = []
    for m in _YEARS_REGEX.finditer(jd_text):
        before = jd_text[max(0, m.start() - 40):m.start()]
        after = jd_text[m.end():m.end() + 80]
        if not (_EXPERIENCE_WORD_REGEX.search(after) or _EXPERIENCE_WORD_REGEX.search(before)):
            continue
        years = int(m.group(1))
        if years <= _MAX_PLAUSIBLE_YEARS:
            found.append(years)

    return max(found) if found else None


def passes_yoe_prefilter(jd_text: str) -> bool:
    """False if the JD explicitly requires >= the configured minimum years (config.yaml: candidate_eligibility)."""
    min_years = extract_min_years_required(jd_text)
    return min_years is None or min_years < SKIP_IF_MIN_YEARS_AT_LEAST
