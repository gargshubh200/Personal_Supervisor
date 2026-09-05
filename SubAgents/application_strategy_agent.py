import os
import json
from typing import List, Literal, Dict, Any
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe, get_client

from SubAgents.jd_analysis_agent import JDAnalysis
from SubAgents.matching_agent import MatchingReport

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


# ------------------------------------------------------------------
# Application Strategy Data Model
# ------------------------------------------------------------------

class ApplicationStrategyOutput(BaseModel):
    decision: Literal["APPLY", "SKIP"] = Field(description="Final decision whether to pursue this role")
    priority: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Application urgency and alignment tier")
    strongest_evidence: List[str] = Field(description="Top candidate accomplishments aligned to the job")
    weakest_area: List[str] = Field(description="Key gaps or requirements where background is weak/missing")
    should_tailor_resume: Literal["YES", "NO"] = Field(description="YES only if decision is APPLY and role requires custom keyword positioning")
    interview_preparation: List[str] = Field(description="Specific technical topics, system design areas, or DSL concepts to review for this interview")


# ------------------------------------------------------------------
# Application Strategy Core Function
# ------------------------------------------------------------------

@observe(name="Application Strategy Agent: Decision & Roadmap Engine")
def determine_application_strategy(
    company_name: str,
    jd_analysis: JDAnalysis,
    matching_report: MatchingReport
) -> ApplicationStrategyOutput:
    """
    Formulates a strategic decision (APPLY/SKIP), priority ranking,
    and actionable interview preparation notes based on the deterministic matching report.
    """
    prompt = f"""
    You are an Executive Career Strategist and Principal Engineering Lead.
    Based on the Deterministic Matching Report and JD Analysis for {company_name}, determine the optimal application strategy.

    DECISION RULES:
    1. If deterministic_score >= 60.0 and passes_threshold is True -> decision = "APPLY".
    2. If decision is "APPLY":
       - Priority HIGH: score >= 80.0
       - Priority MEDIUM: score between 65.0 and 79.9
       - Priority LOW: score between 60.0 and 64.9
       - Set should_tailor_resume = "YES".
    3. If score < 60.0 or critical core skills are missing -> decision = "SKIP", priority = "LOW", should_tailor_resume = "NO".

    INTERVIEW PREPARATION DIRECTIVES:
    Extract 3-5 concrete technical topics, architectural concepts, or specific tools mentioned in the JD 
    that the candidate must study if called for an interview.

    COMPANY: {company_name}
    TARGET ROLE: {jd_analysis.target_role}

    JD ANALYSIS:
    {jd_analysis.model_dump_json(indent=2)}

    DETERMINISTIC MATCHING REPORT:
    {matching_report.model_dump_json(indent=2)}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
            response_schema=ApplicationStrategyOutput,
            temperature=0.1
        )
    )

    strategy = ApplicationStrategyOutput.model_validate_json(response.text)

    langfuse = get_client()
    langfuse.update_current_span(
        metadata={
            "decision": strategy.decision,
            "priority": strategy.priority,
            "should_tailor_resume": strategy.should_tailor_resume,
            "deterministic_score": matching_report.deterministic_score
        }
    )

    return strategy