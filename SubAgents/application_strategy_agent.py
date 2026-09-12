import os
import json
from typing import List, Literal, Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe, get_client

from SubAgents.jd_analysis_agent import JDAnalysis
from SubAgents.matching_agent import MatchingReport

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError, ServerError

gemini_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    retry=retry_if_exception_type((ClientError, ServerError)),
    reraise=True
)

load_dotenv()

client = genai.Client(
    vertexai=True,
    project="career-os-project",
    location="global"
)


class ApplicationStrategyOutput(BaseModel):
    company_name: str = Field(description="Name of the target company")
    role_title: str = Field(description="Title of the target position")
    decision: Literal["APPLY", "SKIP"] = Field(description="Final decision whether to pursue this role")
    priority: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Application urgency and alignment tier")
    strongest_evidence: List[str] = Field(description="Top candidate accomplishments aligned to the job")
    weakest_area: List[str] = Field(description="Key gaps or requirements where background is weak/missing")
    should_tailor_resume: Literal["YES", "NO"] = Field(
        description="YES only if decision is APPLY and role requires custom keyword positioning")
    interview_preparation: List[str] = Field(
        description="Specific technical topics, system design areas, or DSL concepts to review for this interview")
    reasoning: str = Field(description="Detailed strategy rationale for auditing")


@gemini_retry
@observe(name="Application Strategy Agent: Decision Engine")
def determine_application_strategy(
        company_name: str,
        role_title: str,
        jd_analysis: JDAnalysis,
        matching_report: MatchingReport,
        career_constraints: Optional[Dict[str, Any]] = None
) -> ApplicationStrategyOutput:
    """
    Formulates a strategic decision (APPLY/SKIP) while strictly enforcing
    candidate career constraints (e.g., excluding FDE/Solutions/Support roles).
    """

    # ------------------------------------------------------------------
    # HARD CAREER CONSTRAINT CHECK (PRE-GATE REJECTION)
    # ------------------------------------------------------------------
    if career_constraints:
        excluded_keywords = career_constraints.get("excluded_role_keywords", [])
        combined_title = f"{role_title} {getattr(jd_analysis, 'target_role', '')}".lower()

        for bad_kw in excluded_keywords:
            if bad_kw.lower() in combined_title:
                reason = f"Role title '{role_title}' contains excluded keyword '{bad_kw}' under Career Strategy Constraints."
                print(f"🛑 [Strategy Gate] HARD SKIP: {reason}")

                strategy = ApplicationStrategyOutput(
                    company_name=company_name,
                    role_title=role_title,
                    decision="SKIP",
                    priority="LOW",
                    strongest_evidence=[],
                    weakest_area=[f"Role type '{role_title}' is explicitly excluded from targeting."],
                    should_tailor_resume="NO",
                    interview_preparation=[],
                    reasoning=reason
                )

                try:
                    langfuse = get_client()
                    langfuse.update_current_span(
                        metadata={
                            "company_name": company_name,
                            "role_title": role_title,
                            "decision": "SKIP",
                            "constraint_rejection": True,
                            "rejected_keyword": bad_kw
                        }
                    )
                except Exception:
                    pass

                return strategy

    # ------------------------------------------------------------------
    # STANDARD LLM STRATEGY EVALUATION
    # ------------------------------------------------------------------
    prompt = f"""
    You are an Executive Career Strategist.
    Based on the Deterministic Matching Report and JD Analysis for {company_name}, determine the optimal application strategy.

    IMPORTANT: Populate 'company_name' exactly as "{company_name}" and 'role_title' exactly as "{role_title}".

    DECISION RULES:
    1. If deterministic_score >= 60.0 and passes_threshold is True -> decision = "APPLY".
    2. If decision is "APPLY":
       - Priority HIGH: score >= 80.0
       - Priority MEDIUM: score between 65.0 and 79.9
       - Priority LOW: score between 60.0 and 64.9
       - Set should_tailor_resume = "YES".
    3. If score < 60.0 or critical core skills are missing -> decision = "SKIP", priority = "LOW", should_tailor_resume = "NO".

    COMPANY: {company_name}
    TARGET ROLE: {role_title}

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
                "should_tailor_resume": strategy.should_tailor_resume,
                "deterministic_score": matching_report.deterministic_score
            }
        )
    except Exception:
        pass

    return strategy