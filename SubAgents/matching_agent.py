import os
import json
from typing import List, Dict, Any, Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe, get_client

from OtherMCP.ground_truth_mcp import _load_profile
from SubAgents.jd_analysis_agent import JDAnalysis

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError, ServerError

gemini_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    retry=retry_if_exception_type((ClientError, ServerError)),
    reraise=True
)

load_dotenv()

# GCP Vertex AI Client
client = genai.Client(
    vertexai=True,
    project="career-os-project",
    location="global"
)

# Weight mapping for deterministic calculation
WEIGHT_MAP = {
    "MATCH": 1.0,
    "PARTIAL": 0.5,
    "MISSING": 0.0,
    "UNKNOWN": 0.0
}


# ------------------------------------------------------------------
# Structured Data Models
# ------------------------------------------------------------------

class RequirementMatch(BaseModel):
    requirement: str = Field(description="The specific JD requirement or skill evaluated")
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
    deterministic_score: float = Field(description="Overall unweighted baseline score (0.0 to 100.0)")
    core_capability_score: float = Field(description="Match score strictly across CORE_CAPABILITY items (0.0 to 100.0)")
    hard_qualification_gaps: List[str] = Field(description="List of unverified or missing HARD_QUALIFICATION items")
    tool_gaps: List[str] = Field(description="List of missing secondary TOOL_AND_STACK items")
    summary: str


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

    # Combine responsibilities and tech stack into discrete requirements
    target_requirements = jd_analysis.key_responsibilities + jd_analysis.required_tech_stack

    prompt = f"""
    You are a Technical Bar Raiser and Qualification Evaluator.
    Evaluate the Candidate's Master Profile against each target Requirement.

    STEP 1: CATEGORIZE EACH REQUIREMENT
    - 'HARD_QUALIFICATION': Years of experience, security clearances, citizenship/work authorization, physical location, degrees.
    - 'CORE_CAPABILITY': Fundamental engineering competencies (e.g., Python engineering, API design, distributed systems, ETL architecture, LLM agent workflows, production debugging).
    - 'TOOL_AND_STACK': Specific framework, database, or library mentions (e.g., FastAPI, Redis, ANTLR, Docker, GraphQL, Kubernetes).

    STEP 2: CLASSIFY CANDIDATE EVIDENCE
    - MATCH: Candidate has direct, verified experience/metrics matching the requirement.
    - PARTIAL: Candidate has adjacent, transferrable, or partial experience.
    - MISSING: Requirement is completely absent from candidate background.
    - UNKNOWN: Insufficient information in profile.

    CRITICAL INSTRUCTION:
    In candidate_evidence, extract direct, verbatim facts or bullet points from master_profile.json.
    DO NOT fabricate evidence.

    TARGET REQUIREMENTS TO EVALUATE:
    {json.dumps(target_requirements, indent=2)}

    CANDIDATE MASTER PROFILE:
    {json.dumps(master_profile, indent=2)}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
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
        total_points = sum(WEIGHT_MAP.get(e.classification, 0.0) for e in evals)
        overall_score = round((total_points / total_reqs) * 100.0, 2)

        # Core capability sub-score
        core_evals = [e for e in evals if e.category == "CORE_CAPABILITY"]
        if core_evals:
            core_points = sum(WEIGHT_MAP.get(e.classification, 0.0) for e in core_evals)
            core_score = round((core_points / len(core_evals)) * 100.0, 2)
        else:
            core_score = overall_score

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
        langfuse.score(name="match_score", value=overall_score)
        langfuse.score(name="core_capability_score", value=core_score)
    except Exception as e:
        print(f"⚠️ Warning: Could not emit match metrics to Langfuse: {str(e)}")

    return report