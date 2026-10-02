import json
from typing import List, Dict, Any, Literal
from pydantic import BaseModel, Field
from google.genai import types
from langfuse import observe, get_client

from OtherMCP.ground_truth_mcp import _load_profile
from SubAgents.jd_analysis_agent import JDAnalysis
from SubAgents.gemini_common import client, gemini_retry, GEMINI_MODEL

# Single-pass structured extraction (response_schema) -> client.models.generate_content().
# This is not a multi-turn/tool-calling agent loop, so automatic function calling (which
# Google recommends only via Chat.send_message) is not a concern here.

# Weight mapping for deterministic calculation
WEIGHT_MAP = {
    "MATCH": 1.0,
    "PARTIAL": 0.5,
    "MISSING": 0.0,
    "UNKNOWN": 0.0
}

# Required qualifications count fully; nice-to-haves count half, so a missing
# "bonus" skill can't drag the score down as much as a missing must-have.
IMPORTANCE_WEIGHT = {
    "REQUIRED": 1.0,
    "PREFERRED": 0.5
}


# ------------------------------------------------------------------
# Structured Data Models
# ------------------------------------------------------------------

class RequirementMatch(BaseModel):
    requirement: str = Field(description="The specific JD requirement or skill evaluated (without the [REQUIRED]/[PREFERRED] tag)")
    importance: Literal["REQUIRED", "PREFERRED"] = Field(description="Copied from the requirement's [REQUIRED]/[PREFERRED] tag")
    category: Literal["HARD_QUALIFICATION", "CORE_CAPABILITY", "TOOL_AND_STACK"] = Field(
        description="HARD_QUALIFICATION (YOE, clearance, degree, auth), CORE_CAPABILITY (backend, AI, distributed systems), TOOL_AND_STACK (Redis, FastAPI, PySpark)"
    )
    candidate_evidence: List[str] = Field(
        description="Direct ground-truth quotes/facts from master profile matching this requirement"
    )
    classification: Literal["MATCH", "PARTIAL", "MISSING", "UNKNOWN"] = Field(
        description="MATCH (solid evidence), PARTIAL (adjacent experience), MISSING (no background), UNKNOWN (unclear)"
    )
    reasoning: str = Field(description="Concise justification for the classification")


class LLMMatchEvaluations(BaseModel):
    evaluations: List[RequirementMatch] = Field(description="Categorized evaluations for all extracted requirements")


class MatchingReport(BaseModel):
    requirement_matches: List[RequirementMatch]
    total_requirements: int
    match_count: int
    partial_count: int
    missing_count: int
    deterministic_score: float = Field(description="Fit score: importance-weighted coverage of the JD's qualifications (0.0 to 100.0)")
    core_capability_score: float = Field(description="Importance-weighted fit score across CORE_CAPABILITY items only (0.0 to 100.0)")
    hard_qualification_gaps: List[str] = Field(description="List of unverified or missing HARD_QUALIFICATION items")
    tool_gaps: List[str] = Field(description="List of missing secondary TOOL_AND_STACK items")
    summary: str


def _weighted_score(evals: List[RequirementMatch]) -> float:
    total_weight = sum(IMPORTANCE_WEIGHT[e.importance] for e in evals)
    points = sum(IMPORTANCE_WEIGHT[e.importance] * WEIGHT_MAP.get(e.classification, 0.0) for e in evals)
    return round((points / total_weight) * 100.0, 2) if total_weight else 0.0


# ------------------------------------------------------------------
# Matching Agent Core Function
# ------------------------------------------------------------------
@gemini_retry
@observe(name="Matching Agent: Categorized Requirement Evaluator")
def evaluate_candidate_match(
        jd_analysis: JDAnalysis,
        master_profile: Dict[str, Any] = None,
        threshold: float = 60.0
) -> MatchingReport:
    """
    Evaluates candidate evidence against JD requirements, categorizing each item
    into HARD_QUALIFICATION, CORE_CAPABILITY, or TOOL_AND_STACK to support EV filtering.
    """
    if not master_profile:
        master_profile = _load_profile()

    # Judge the candidate on the JD's QUALIFICATIONS, not on its duties: duties
    # ("participate in on-call") aren't something a candidate has or lacks, and
    # scoring every listed tool made the score track JD verbosity, not fit.
    target_requirements = (
        [f"[REQUIRED] {q}" for q in jd_analysis.required_qualifications]
        + [f"[PREFERRED] {q}" for q in jd_analysis.preferred_qualifications]
    )
    if not target_requirements:
        # JD without an identifiable qualifications section — fall back to the
        # tech stack, then to the responsibilities, rather than scoring nothing.
        fallback = jd_analysis.required_tech_stack or jd_analysis.key_responsibilities
        target_requirements = [f"[REQUIRED] {item}" for item in fallback]

    prompt = f"""
    You are a Technical Bar Raiser and Qualification Evaluator.
    Evaluate the Candidate's Master Profile against each target Requirement.

    STEP 1: CATEGORIZE EACH REQUIREMENT
    - 'HARD_QUALIFICATION': Years of experience, security clearances, citizenship/work authorization, physical location, degrees.
    - 'CORE_CAPABILITY': Fundamental engineering competencies (e.g., Python engineering, API design, distributed systems, ETL architecture, LLM agent workflows, production debugging).
    - 'TOOL_AND_STACK': Specific framework, database, or library mentions (e.g., FastAPI, Redis, ANTLR, Docker, GraphQL, Kubernetes).

    STEP 2: CLASSIFY CANDIDATE EVIDENCE
    - MATCH: The profile shows direct, hands-on experience with this exact skill/technology/domain.
    - PARTIAL: The profile shows a close equivalent a hiring manager would likely accept
      (e.g. Flask for FastAPI, GCP for AWS, Airflow for Dagster), or the skill used only lightly.
    - MISSING: Nothing in the profile shows this skill or a close equivalent.
    - UNKNOWN: The requirement is too vague to judge against any profile.
    - "One of: A, B, C" items: MATCH if ANY alternative is matched — never penalize the others.

    STEP 3: COPY IMPORTANCE
    - Each requirement starts with [REQUIRED] or [PREFERRED]; copy it into 'importance' and drop the
      tag from 'requirement'. Evaluate every requirement exactly once.

    CRITICAL INSTRUCTION:
    In candidate_evidence, extract direct, verbatim facts or bullet points from master_profile.json.
    DO NOT fabricate evidence.

    TARGET REQUIREMENTS TO EVALUATE:
    {json.dumps(target_requirements, indent=2)}

    CANDIDATE MASTER PROFILE:
    {json.dumps(master_profile, indent=2)}
    """

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1536),
            response_mime_type="application/json",
            response_schema=LLMMatchEvaluations,
            temperature=0.1
        )
    )

    evals = LLMMatchEvaluations.model_validate_json(response.text).evaluations

    # --- CATEGORIZED METRIC CALCULATION ---
    total_reqs = len(evals)
    if total_reqs == 0:
        overall_score = 0.0
        core_score = 0.0
    else:
        overall_score = _weighted_score(evals)

        # Core capability sub-score
        core_evals = [e for e in evals if e.category == "CORE_CAPABILITY"]
        core_score = _weighted_score(core_evals) if core_evals else overall_score

    # Isolate specific gaps for downstream strategy checks
    hard_gaps = [
        e.requirement for e in evals
        if e.category == "HARD_QUALIFICATION" and e.classification in ("MISSING", "UNKNOWN")
    ]

    tool_gaps = [
        e.requirement for e in evals
        if e.category == "TOOL_AND_STACK" and e.classification in ("MISSING", "UNKNOWN")
    ]

    match_c = sum(1 for e in evals if e.classification == "MATCH")
    partial_c = sum(1 for e in evals if e.classification == "PARTIAL")
    missing_c = sum(1 for e in evals if e.classification in ("MISSING", "UNKNOWN"))

    summary_msg = (
        f"Overall Score: {overall_score}% | Core Capability Score: {core_score}% | "
        f"Hard Gaps: {len(hard_gaps)} | Tool Gaps: {len(tool_gaps)} ({match_c} Matches, {partial_c} Partials out of {total_reqs} reqs)."
    )

    report = MatchingReport(
        requirement_matches=evals,
        total_requirements=total_reqs,
        match_count=match_c,
        partial_count=partial_c,
        missing_count=missing_c,
        deterministic_score=overall_score,
        core_capability_score=core_score,
        hard_qualification_gaps=hard_gaps,
        tool_gaps=tool_gaps,
        summary=summary_msg
    )

    # --- LANGFUSE TELEMETRY ---
    try:
        langfuse = get_client()
        langfuse.update_current_span(
            metadata={
                "deterministic_score": overall_score,
                "core_capability_score": core_score,
                "hard_qualification_gaps_count": len(hard_gaps),
                "total_requirements": total_reqs
            }
        )

        # Log both metrics for telemetry monitoring
        langfuse.score_current_span(name="match_score", value=overall_score)
        langfuse.score_current_span(name="core_capability_score", value=core_score)
    except Exception as e:
        print(f"⚠️ Warning: Could not emit match metrics to Langfuse: {str(e)}")

    return report