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
    decision: Literal["APPLY", "SKIP"] = Field(description="Final decision based on holistic Expected Value (EV)")
    priority: Literal["HIGH", "MEDIUM", "LOW"] = Field(description="Application urgency tier")
    dealbreaker_triggered: bool = Field(description="True if an unresolvable hard constraint was detected")
    dealbreaker_reason: Optional[str] = Field(description="Specific dealbreaker reason if triggered (e.g. security clearance, work authorization)")
    core_capability_match: bool = Field(description="True if candidate possesses the primary technical muscle required for the role")
    strongest_evidence: List[str] = Field(description="Top candidate accomplishments aligned to core capabilities")
    weakest_area: List[str] = Field(description="Key gaps or requirements where background is weak/missing")
    should_tailor_resume: Literal["YES", "NO"] = Field(description="YES only if decision is APPLY and role warrants customization")
    interview_preparation: List[str] = Field(description="Key technical topics or system design concepts to review")
    reasoning: str = Field(description="Detailed strategy rationale for auditing")


@gemini_retry
@observe(name="Application Strategy Agent: Expected Value Engine")
def determine_application_strategy(
    company_name: str,
    role_title: str,
    jd_analysis: JDAnalysis,
    matching_report: MatchingReport,
    career_constraints: Optional[Dict[str, Any]] = None
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
                    strongest_evidence=[],
                    weakest_area=[reason],
                    should_tailor_resume="NO",
                    interview_preparation=[],
                    reasoning=reason
                )

    # ------------------------------------------------------------------
    # STAGE 2 & 3: EXPECTED VALUE & DEALBREAKER EVALUATION (LLM)
    # ------------------------------------------------------------------
    boost_companies = (career_constraints or {}).get("preference_boost_companies", [])
    boost_keywords = (career_constraints or {}).get("preference_boost_keywords", [])
    downgrade_keywords = (career_constraints or {}).get("preference_downgrade_keywords", [])

    prompt = f"""
    You are an Executive Career Strategist and Principal Engineering Hiring Lead.
    Evaluate the viability of applying to {company_name} for the position of {role_title}.

    Candidate Master Background (Sahil Garg):
    - 2.5 Years Experience as Software Engineer building Applied AI, Enterprise Backends, and Platform Tooling at o9 Solutions.
    - Strong core skills: Python, SQL, REST APIs, LangChain/LangGraph, Spark, Kubernetes, ANTLR, PageRank, Semantic Retrieval.

    EVALUATION DIRECTIVE (EXPECTED VALUE FUNNEL):
    Do NOT blindly follow the arithmetic match score. Evaluate the Expected Value (EV) using this decision funnel:

    STAGE 1: DEALBREAKER CHECK
    Set 'dealbreaker_triggered' = True and 'decision' = "SKIP" if ANY of these are present in the JD:
    - Active US Security Clearance / TS-SCI requirement.
    - Strict US/EU Work Authorization required (when position is listed in India/Remote).
    - Hard requirement of 8+ years of experience for non-principal roles, or 5+ years as the PRIMARY requirement.
    - Non-engineering core mandate (e.g. 100% sales, cold-calling, pure frontend React/CSS).
    - Primary programming language required is Java, Go, Ruby, PHP, or TypeScript (NOT Python as first-class language).
    - Role is fundamentally hardware, embedded systems, networking hardware, or datacenter operations focused.
    - Role is analyst / BI / data-analyst / reporting-focused rather than engineering-focused.
    - Company is an IT services / outsourcing shop (e.g. TCS, Infosys, Wipro, Cognizant, Capgemini, HCL) rather than a product company.

    STAGE 2: CORE CAPABILITY FIT
    Does the candidate possess the core technical muscle for this role (e.g. Python, Backends, Distributed Systems, AI/LLM workflows)?
    - If YES: Missing adjacent secondary frameworks (e.g. FastAPI vs Flask, Redis, minor tool gaps) are EASILY LEARNABLE. Do NOT skip for minor tool gaps if core capability fit is strong.
    - If NO: If core engineering competencies are missing, mark 'decision' = "SKIP".

    STAGE 3: DECISION RULE
    - decision = "APPLY" if dealbreaker_triggered is False AND core_capability_match is True AND expected value of applying is positive.
    - decision = "SKIP" if dealbreaker_triggered is True OR core capability match is lacking OR expected value is negligible.

    STAGE 4: PRIORITY ADJUSTMENT (only applies if decision = "APPLY")
    - Start from a baseline priority driven by match quality and role fit.
    - UPGRADE MEDIUM -> HIGH if company is AI-native/data-infrastructure focused (e.g. {boost_companies}),
      OR the JD explicitly mentions any of {boost_keywords},
      OR company is a YC-backed Series A-C startup, OR role is remote-first/explicitly open to India-based candidates,
      OR JD mentions a 1-3 or 2-4 year experience requirement.
    - DOWNGRADE HIGH -> MEDIUM if the role requires any of {downgrade_keywords} as a primary/co-equal skill,
      OR requires 3-5 years with candidate evidence classified PARTIAL (not full MATCH) on key requirements,
      OR the company is a large enterprise (10,000+ employees) with typically slower hiring/less ownership.

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
                "deterministic_score": matching_report.deterministic_score
            }
        )
    except Exception:
        pass

    return strategy