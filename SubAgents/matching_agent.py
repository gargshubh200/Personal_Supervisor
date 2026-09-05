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

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

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
    candidate_evidence: List[str] = Field(description="Direct ground-truth quotes/facts from master profile matching this requirement")
    classification: Literal["MATCH", "PARTIAL", "MISSING", "UNKNOWN"] = Field(
        description="MATCH (solid evidence), PARTIAL (adjacent experience), MISSING (no background), UNKNOWN (unclear)"
    )
    reasoning: str = Field(description="Concise justification for the classification")


class LLMMatchEvaluations(BaseModel):
    evaluations: List[RequirementMatch] = Field(description="Evaluations for all extracted requirements")


class MatchingReport(BaseModel):
    requirement_matches: List[RequirementMatch]
    total_requirements: int
    match_count: int
    partial_count: int
    missing_count: int
    deterministic_score: float = Field(description="Calculated score (0.0 to 100.0)")
    passes_threshold: bool = Field(description="True if deterministic_score >= target threshold")
    summary: str


# ------------------------------------------------------------------
# Matching Agent Core Function
# ------------------------------------------------------------------

@observe(name="Matching Agent: Grounded Requirement Evaluator")
def evaluate_candidate_match(
    jd_analysis: JDAnalysis,
    master_profile: Dict[str, Any] = None,
    threshold: float = 60.0
) -> MatchingReport:
    """
    Evaluates candidate evidence against JD requirements and computes a
    defensible, deterministic match score.
    """
    if not master_profile:
        master_profile = _load_profile()

    # Combine key responsibilities and required tech stack into discrete target requirements
    target_requirements = jd_analysis.key_responsibilities + jd_analysis.required_tech_stack

    prompt = f"""
    You are a rigorous Technical Bar Raiser and Qualification Evaluator.
    Evaluate the Candidate's Master Profile against each target Requirement.

    EVALUATION CLASSIFICATION RULES:
    - MATCH: Candidate has direct, verified experience/metrics matching the requirement.
    - PARTIAL: Candidate has adjacent, transferrable, or partial tool/framework experience.
    - MISSING: Requirement is completely absent from candidate background.
    - UNKNOWN: Insufficient information in profile to confirm or deny.

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
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
            response_schema=LLMMatchEvaluations,
            temperature=0.0
        )
    )

    evals = LLMMatchEvaluations.model_validate_json(response.text).evaluations

    # --- DETERMINISTIC SCORE CALCULATION (PURE PYTHON) ---
    total_reqs = len(evals)
    if total_reqs == 0:
        calculated_score = 0.0
    else:
        total_points = sum(WEIGHT_MAP.get(e.classification, 0.0) for e in evals)
        calculated_score = round((total_points / total_reqs) * 100.0, 2)

    passes = calculated_score >= threshold

    match_c = sum(1 for e in evals if e.classification == "MATCH")
    partial_c = sum(1 for e in evals if e.classification == "PARTIAL")
    missing_c = sum(1 for e in evals if e.classification in ("MISSING", "UNKNOWN"))

    summary_msg = f"Match Score: {calculated_score}% ({match_c} Matches, {partial_c} Partials, {missing_c} Gaps out of {total_reqs} requirements)."

    report = MatchingReport(
        requirement_matches=evals,
        total_requirements=total_reqs,
        match_count=match_c,
        partial_count=partial_c,
        missing_count=missing_c,
        deterministic_score=calculated_score,
        passes_threshold=passes,
        summary=summary_msg
    )

    langfuse = get_client()
    langfuse.update_current_span(
        metadata={
            "deterministic_score": calculated_score,
            "passes_threshold": passes,
            "total_requirements": total_reqs
        }
    )

    return report