import json
from typing import List, Literal, Dict, Any, Optional
from pydantic import BaseModel, Field
from google.genai import types
from langfuse import observe, get_client

from SubAgents.jd_analysis_agent import JDAnalysis
from SubAgents.matching_agent import MatchingReport
from SubAgents.gemini_common import client, gemini_retry, GEMINI_MODEL
from OtherMCP.ground_truth_mcp import _load_profile

# Single-pass structured extraction (response_schema) -> client.models.generate_content().
# This is not a multi-turn/tool-calling agent loop, so automatic function calling (which
# Google recommends only via Chat.send_message) is not a concern here.


class ApplicationStrategyOutput(BaseModel):
    company_name: str = Field(description="Name of the target company")
    role_title: str = Field(description="Title of the target position")
    decision: Literal["APPLY", "SKIP"] = Field(description="Final decision based on holistic Expected Value (EV)")
    priority: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Application urgency tier")
    dealbreaker_triggered: bool = Field(description="True if an unresolvable hard constraint was detected")
    dealbreaker_reason: Optional[str] = Field(description="Specific dealbreaker reason if triggered (e.g. security clearance, work authorization)")
    core_capability_match: bool = Field(description="True if candidate possesses the primary technical muscle required for the role")
    fit_band: Literal["STRONG", "GOOD", "WEAK"] = Field(description="Fit band derived from the matching report per the rubric")
    desirability_positives: List[str] = Field(description="Each positive desirability signal that applies, as '<signal>: <evidence>'")
    desirability_negatives: List[str] = Field(description="Each negative desirability signal that applies, as '<signal>: <evidence>'")
    strongest_evidence: List[str] = Field(description="Top candidate accomplishments aligned to core capabilities")
    weakest_area: List[str] = Field(description="Key gaps or requirements where background is weak/missing")
    should_tailor_resume: Literal["YES", "NO"] = Field(description="YES only if decision is APPLY and priority is HIGH or MEDIUM")
    interview_preparation: List[str] = Field(description="Key technical topics or system design concepts to review")
    reasoning: str = Field(description="Detailed strategy rationale for auditing")


@gemini_retry
@observe(name="Application Strategy Agent: Expected Value Engine")
def determine_application_strategy(
    company_name: str,
    role_title: str,
    jd_analysis: JDAnalysis,
    matching_report: MatchingReport,
    career_constraints: Optional[Dict[str, Any]] = None,
    job_url: str = "",
    source_platform: str = ""
) -> ApplicationStrategyOutput:
    """
    Evaluates role viability through a multi-stage Expected Value (EV) decision funnel:
    1. Python Config Constraints
    2. Hard Dealbreaker Identification (Clearance, Citizenship, YOE Bloat)
    3. Core Capability vs. Fluff Skill Weighting
    """

    # ------------------------------------------------------------------
    # STAGE 1: PYTHON CONFIG CONSTRAINTS (HARD PRE-GATE)
    # ------------------------------------------------------------------
    if career_constraints:
        excluded_keywords = career_constraints.get("excluded_role_keywords", [])
        excluded_companies = career_constraints.get("excluded_companies", [])
        combined_title = f"{role_title} {getattr(jd_analysis, 'target_role', '')}".lower()

        # Check Excluded Companies
        for bad_comp in excluded_companies:
            if bad_comp.lower() in company_name.lower():
                reason = f"Company '{company_name}' is explicitly excluded under Career Strategy Constraints."
                print(f"🛑 [Strategy Gate] HARD SKIP: {reason}")
                return ApplicationStrategyOutput(
                    company_name=company_name,
                    role_title=role_title,
                    decision="SKIP",
                    priority="LOW",
                    dealbreaker_triggered=True,
                    dealbreaker_reason=reason,
                    core_capability_match=False,
                    fit_band="WEAK",
                    desirability_positives=[],
                    desirability_negatives=[],
                    strongest_evidence=[],
                    weakest_area=[reason],
                    should_tailor_resume="NO",
                    interview_preparation=[],
                    reasoning=reason
                )

        # Check Excluded Role Keywords
        for bad_kw in excluded_keywords:
            if bad_kw.lower() in combined_title:
                reason = f"Role title '{role_title}' contains excluded keyword '{bad_kw}'."
                print(f"🛑 [Strategy Gate] HARD SKIP: {reason}")
                return ApplicationStrategyOutput(
                    company_name=company_name,
                    role_title=role_title,
                    decision="SKIP",
                    priority="LOW",
                    dealbreaker_triggered=True,
                    dealbreaker_reason=reason,
                    core_capability_match=False,
                    fit_band="WEAK",
                    desirability_positives=[],
                    desirability_negatives=[],
                    strongest_evidence=[],
                    weakest_area=[reason],
                    should_tailor_resume="NO",
                    interview_preparation=[],
                    reasoning=reason
                )

    # ------------------------------------------------------------------
    # STAGE 2-4: DEALBREAKERS, FIT BAND, DESIRABILITY & PRIORITY (LLM, explicit rubric)
    # ------------------------------------------------------------------
    constraints = career_constraints or {}
    boost_companies = constraints.get("preference_boost_companies", [])
    boost_keywords = constraints.get("preference_boost_keywords", [])
    downgrade_keywords = constraints.get("preference_downgrade_keywords", [])
    large_company_size = constraints.get("preference_downgrade_min_company_size", 10000)
    bands = constraints.get("fit_bands", {"strong_min_score": 70, "strong_min_core": 60, "good_min_score": 45})

    # Candidate background comes from master_profile.json (regenerated from the
    # latest resume), never hardcoded — so it can't drift from the real profile.
    profile = _load_profile()
    candidate_background = {
        "name": profile["personal_info"]["name"],
        "summary": profile.get("summary", ""),
        "experience": [
            {"role": e.get("role"), "company": e.get("company"), "period": e.get("period")}
            for e in profile.get("experience", [])
        ],
        "skills": profile.get("skills", {})
    }

    prompt = f"""
    You are an Executive Career Strategist and Principal Engineering Hiring Lead.
    Decide whether the candidate should apply to {company_name} for "{role_title}", and how urgently.
    Follow the rubric below EXACTLY and in order. Do not invent extra rules.

    CANDIDATE BACKGROUND (from master_profile.json):
    {json.dumps(candidate_background, indent=2)}

    ALREADY VERIFIED (deterministic eligibility gate, before this step): the role's required years of
    experience, employment type (full-time) and India location eligibility all PASS. Do not re-judge them,
    and do not treat "remote" or "open to India" as a positive signal — every role reaching you has it.

    STEP 1 — DEALBREAKERS. Set dealbreaker_triggered = true and decision = "SKIP" if ANY apply:
    - Active security clearance requirement (TS/SCI etc.).
    - Non-engineering core mandate (sales, cold-calling, support desk, pure frontend React/CSS).
    - Primary programming language is Java, Go, Ruby, PHP, or TypeScript and Python is not first-class.
    - Fundamentally hardware, embedded, networking hardware, or datacenter operations.
    - Analyst / BI / reporting role rather than engineering.
    - Company is an IT services / outsourcing / staffing shop (TCS, Infosys, Wipro, Cognizant, Capgemini, HCL, ...).

    STEP 2 — FIT BAND, from the MATCHING REPORT below (deterministic_score = importance-weighted coverage of
    the JD's qualifications; core_capability_score = same, over core engineering capabilities only):
    - STRONG: deterministic_score >= {bands["strong_min_score"]} AND core_capability_score >= {bands["strong_min_core"]}
    - GOOD:   deterministic_score >= {bands["good_min_score"]} (and not STRONG)
    - WEAK:   deterministic_score < {bands["good_min_score"]}, or the matching report has 0 requirements
    Set core_capability_match = true unless the core_capability_score is below 50.

    STEP 3 — DESIRABILITY. List every signal that applies, with evidence, into desirability_positives /
    desirability_negatives. Only these signals count:
    POSITIVE (+1 each):
    - Company is one of {boost_companies}, or is an AI-native / data-infrastructure product company.
    - Any of {boost_keywords} is central to the role (in required qualifications or main responsibilities).
    - Company is a YC-backed or Series A-C product startup (only if evident from the JD).
    - title_seniority is JUNIOR or MID.
    NEGATIVE (-1 each):
    - Any of {downgrade_keywords} is a primary / co-equal required skill.
    - title_seniority is SENIOR or LEAD_OR_ABOVE.
    - Company is a large enterprise (~{large_company_size}+ employees).
    - Each item in the JD analysis 'listing_red_flags'.
    - The listing comes via an aggregator / reposting site or staffing intermediary rather than the employer
      (judge from the job URL / source below and the company name, e.g. Jobgether, Glassdoor partner links).
    net_desirability = (#positives) - (#negatives).

    STEP 4 — DECISION & PRIORITY (no exceptions):
    - dealbreaker_triggered -> decision SKIP, priority LOW.
    - WEAK fit             -> decision SKIP, priority LOW.
    - STRONG fit           -> APPLY; HIGH if net_desirability >= 0, else MEDIUM.
    - GOOD fit             -> APPLY; HIGH if net_desirability >= 2, MEDIUM if net_desirability is -1..1,
                              LOW if net_desirability <= -2.
    - should_tailor_resume = "YES" only if decision is APPLY and priority is HIGH or MEDIUM.

    In 'reasoning', state the fit band (with both scores), the net_desirability arithmetic, and the
    resulting tier in one or two sentences, then any other notes.

    COMPANY: {company_name}
    TARGET ROLE: {role_title}
    JOB URL: {job_url or "unknown"}
    SOURCE PLATFORM: {source_platform or "unknown"}

    JD ANALYSIS:
    {jd_analysis.model_dump_json(indent=2)}

    MATCHING REPORT:
    {matching_report.model_dump_json(indent=2)}
    """

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            response_mime_type="application/json",
            response_schema=ApplicationStrategyOutput,
            temperature=0.1,

        )
    )

    strategy_dict = json.loads(response.text)
    strategy_dict["company_name"] = company_name
    strategy_dict["role_title"] = role_title

    strategy = ApplicationStrategyOutput(**strategy_dict)

    try:
        langfuse = get_client()
        langfuse.update_current_span(
            metadata={
                "company_name": strategy.company_name,
                "role_title": strategy.role_title,
                "decision": strategy.decision,
                "priority": strategy.priority,
                "dealbreaker_triggered": strategy.dealbreaker_triggered,
                "core_capability_match": strategy.core_capability_match,
                "fit_band": strategy.fit_band,
                "desirability_positives": strategy.desirability_positives,
                "desirability_negatives": strategy.desirability_negatives,
                "deterministic_score": matching_report.deterministic_score
            }
        )
    except Exception:
        pass

    return strategy