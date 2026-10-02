"""
OtherMCP/eligibility_mcp.py

Deterministic candidate-eligibility gate, run right after JD analysis and
BEFORE the matching / strategy / tailoring LLM calls. Hard constraints
(experience, employment type, location) are checked here in Python against
config.yaml rather than left to the strategy LLM, which previously never saw
them: the JD analysis didn't extract them and the strategy prompt only
received the analysis, not the raw JD.
"""

from typing import Any, Dict, List, Optional
from mcp.server.mcpserver import MCPServer

from config_loader import get_candidate_eligibility, get_india_location_keywords
from SubAgents.jd_analysis_agent import JDAnalysis

mcp = MCPServer("EligibilityServer")

ELIGIBILITY = get_candidate_eligibility()
INDIA_KEYWORDS = [k.lower() for k in get_india_location_keywords()]


def _location_names_india(listed_location: Optional[str]) -> bool:
    loc = str(listed_location or "").lower()
    return any(k in loc for k in INDIA_KEYWORDS)


@mcp.tool()
def check_candidate_eligibility(jd_analysis: JDAnalysis, listed_location: Optional[str] = None) -> Dict[str, Any]:
    """
    Returns {"eligible": bool, "reasons": [...]} for the hard constraints in
    config.yaml (candidate_eligibility). Every failed check contributes a
    human-readable reason (with the JD evidence) for the Firestore record.
    """
    reasons: List[str] = []

    if not jd_analysis.is_job_posting:
        reasons.append("Not a job posting (article / blog / listing page).")

    min_years = jd_analysis.min_years_experience
    threshold = ELIGIBILITY["skip_if_min_years_at_least"]
    if min_years is not None and min_years >= threshold:
        evidence = f' Evidence: "{jd_analysis.experience_evidence}"' if jd_analysis.experience_evidence else ""
        reasons.append(f"Requires {min_years:g}+ years of experience (skip threshold: {threshold}+).{evidence}")

    if jd_analysis.employment_type not in ELIGIBILITY["allowed_employment_types"]:
        reasons.append(f"Employment type is {jd_analysis.employment_type} (allowed: {', '.join(ELIGIBILITY['allowed_employment_types'])}).")

    eligibility = jd_analysis.india_eligibility
    # The JD may be silent on location while the job source lists a real Indian
    # city (e.g. Wellfound/LinkedIn India searches) — that settles UNCLEAR.
    if eligibility == "UNCLEAR" and _location_names_india(listed_location):
        eligibility = "ELIGIBLE"
    if eligibility != "ELIGIBLE":
        where = ", ".join(jd_analysis.eligible_locations) or listed_location or "unspecified"
        evidence = f' Evidence: "{jd_analysis.location_evidence}"' if jd_analysis.location_evidence else ""
        label = "Not open to candidates in India" if eligibility == "NOT_ELIGIBLE" else "India eligibility unclear"
        reasons.append(f"{label} (locations: {where}).{evidence}")

    return {"eligible": not reasons, "reasons": reasons}


if __name__ == "__main__":
    mcp.run()
