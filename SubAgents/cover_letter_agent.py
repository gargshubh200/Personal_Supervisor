import os
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe, get_client

from OtherMCP.ground_truth_mcp import verify_claim, _load_profile
from SubAgents.jd_analysis_agent import JDAnalysis, analyze_job_description

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError, ServerError

gemini_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    retry=retry_if_exception_type((ClientError, ServerError)),
    reraise=True
)

load_dotenv()

# client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# NEW: GCP Vertex AI Client (Draws from your ₹28,694 GCP credits!)
client = genai.Client(
    vertexai=True,
    project="career-os-project",
    location="global"
)

class CoverLetterOutput(BaseModel):
    salutation: str = Field(description="Professional greeting (e.g. 'Dear Hiring Team at [Company]')")
    opening_paragraph: str = Field(description="Engaging hook linking candidate core background to company & target role")
    body_paragraphs: List[str] = Field(description="2 structured paragraphs highlighting ground-truth metrics and engineering impact matching JD requirements")
    closing_paragraph: str = Field(description="Concise call-to-action and professional sign-off")
    full_cover_letter_text: str = Field(description="The complete, formatted cover letter text")


class CoverLetterReviewOutput(BaseModel):
    overall_score: int = Field(description="Overall cover letter quality score (0 to 100)")
    meets_quality_bar: bool = Field(description="True if overall_score >= target quality threshold")
    relevance_score: int = Field(description="Score out of 10 for target JD and tech stack alignment")
    tone_and_clarity_score: int = Field(description="Score out of 10 for non-spammy, executive-ready tone")
    qualitative_critique: str = Field(description="Detailed qualitative feedback on narrative flow and impact")
    key_improvements: List[str] = Field(description="Top 3 actionable suggestions for refining the cover letter")


@gemini_retry
@observe(name="Cover Letter: Writer Pass")
def generate_cover_letter(
    jd_analysis: JDAnalysis,
    master_profile: Dict[str, Any],
    company_name: str,
    audit_feedback: Optional[List[Dict[str, Any]]] = None,
    review_feedback: Optional[CoverLetterReviewOutput] = None
) -> CoverLetterOutput:
    critique_block = ""
    if audit_feedback:
        critique_block += f"\nCRITICAL SAFETY AUDIT FAILURES TO FIX:\n{json.dumps(audit_feedback, indent=2)}\nDO NOT hallucinate metrics or tools.\n"
    if review_feedback:
        critique_block += f"\nCOVER LETTER CRITIQUE (Previous Score: {review_feedback.overall_score}/100):\n"
        for imp in review_feedback.key_improvements:
            critique_block += f"- {imp}\n"
        critique_block += f"Qualitative Feedback: {review_feedback.qualitative_critique}\n"

    prompt = f"""
    You are an Executive Cover Letter Writer for Forward Deployed, Applied AI & Software Engineering roles pitch.
    Draft a concise, compelling cover letter for Sahil Garg applying to {company_name} for the position of {jd_analysis.target_role}.

    {critique_block}
    GROUND-TRUTH DIRECTIVES:
    1. Reference strictly verified facts from master profile (90+ client environments, 98% onboarding speedup, ANTLR log obfuscation for 15+ clients, 60% query reduction via skills-based agent).
    2. Connect candidate expertise in Python, agentic LLM workflows, and platform reliability directly to the JD requirements.
    3. Maintain a confident, professional engineering tone. Avoid generic boilerplate.

    TARGET JOB ANALYSIS:
    {jd_analysis.model_dump_json(indent=2)}

    MASTER PROFILE:
    {json.dumps(master_profile, indent=2)}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            response_mime_type="application/json",
            response_schema=CoverLetterOutput,
            temperature=0.25
        )
    )

    return CoverLetterOutput.model_validate_json(response.text)

@gemini_retry
@observe(name="Cover Letter: Ground-Truth Audit")
def verify_cover_letter_safety(cover_letter: CoverLetterOutput) -> Dict[str, Any]:
    audit_results = []
    has_violations = False

    all_paragraphs = [cover_letter.opening_paragraph] + cover_letter.body_paragraphs + [cover_letter.closing_paragraph]

    for para in all_paragraphs:
        check = verify_claim(para)
        audit_results.append({
            "paragraph": para[:100] + "...",
            "verified": check["verified"],
            "details": check
        })
        if not check["verified"]:
            has_violations = True

    return {
        "passed_audit": not has_violations,
        "results": audit_results
    }


@gemini_retry
@observe(name="Cover Letter: Quality Reviewer")
def review_cover_letter_agent(
    jd_analysis: JDAnalysis,
    cover_letter: CoverLetterOutput,
    quality_threshold: int = 85
) -> CoverLetterReviewOutput:
    prompt = f"""
    You are an Executive Technology Recruiter evaluating a Cover Letter.
    Critique the candidate's draft against the target Job Description analysis.

    EVALUATION STANDARDS:
    1. Hook & Positioning: Does the opening immediately demonstrate technical relevance?
    2. Specificity & Evidence: Are concrete, ground-truth engineering accomplishments cited cleanly but not loudly?
    3. Tone & Alignment: Is the tone executive, cold, and free of filler phrases?
    4. Call-to-Action: Is the closing concise and low-friction?
    5. Overall Perception: Does the letter show candidate's ownership capabilities & read as a confident, technically competent pitch?

    Target Quality Threshold: {quality_threshold}/100.
    Set meets_quality_bar to true ONLY if overall_score >= {quality_threshold}.

    TARGET JOB ANALYSIS:
    {jd_analysis.model_dump_json(indent=2)}

    COVER LETTER TO EVALUATE:
    {cover_letter.model_dump_json(indent=2)}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
            response_schema=CoverLetterReviewOutput,
            temperature=0.1
        )
    )

    review = CoverLetterReviewOutput.model_validate_json(response.text)

    langfuse = get_client()
    langfuse.update_current_span(
        metadata={
            "overall_score": review.overall_score,
            "meets_quality_bar": review.meets_quality_bar,
            "quality_threshold": quality_threshold
        }
    )

    return review


@gemini_retry
@observe(name="Cover Letter Agent: Workflow Run")
def run_cover_letter_workflow(
    jd_text: str,
    company_name: str,
    max_iterations: int = 3,
    quality_threshold: int = 85
) -> Dict[str, Any]:
    print(f"🚀 [Cover Letter Agent] Extracting JD Requirements for {company_name}...")
    jd_analysis = analyze_job_description(jd_text)
    master_profile = _load_profile()

    audit_feedback = None
    review_feedback = None
    best_candidate = None
    highest_score = -1

    for iteration in range(1, max_iterations + 1):
        print(f"\n📝 [Cover Letter Iteration {iteration}/{max_iterations}] Drafting Content...")
        cl_output = generate_cover_letter(
            jd_analysis, master_profile, company_name, audit_feedback, review_feedback
        )

        print("🛡️ [Cover Letter Audit] Verifying Ground-Truth Claims...")
        audit = verify_cover_letter_safety(cl_output)

        print("🧐 [Cover Letter Review] Evaluating Quality & Narrative Tone...")
        review = review_cover_letter_agent(
            jd_analysis, cl_output, quality_threshold=quality_threshold
        )

        print(f"📊 Cover Letter Score: {review.overall_score}/100 | Meets Bar ({quality_threshold}+): {review.meets_quality_bar} | Safety Passed: {audit['passed_audit']}")

        if audit["passed_audit"] and review.overall_score > highest_score:
            highest_score = review.overall_score
            best_candidate = {
                "cover_letter": cl_output.model_dump(),
                "audit_report": audit,
                "review_report": review.model_dump(),
                "iterations_taken": iteration,
                "early_stopped": False
            }

        if audit["passed_audit"] and review.meets_quality_bar:
            print(f"🎯 EARLY STOPPING: Cover Letter score ({review.overall_score}/100) met threshold at iteration {iteration}!")
            best_candidate["early_stopped"] = True
            return best_candidate

        audit_feedback = audit["results"] if not audit["passed_audit"] else None
        review_feedback = review

    return best_candidate or {
        "cover_letter": cl_output.model_dump(),
        "audit_report": audit,
        "review_report": review.model_dump(),
        "iterations_taken": max_iterations,
        "early_stopped": False
    }