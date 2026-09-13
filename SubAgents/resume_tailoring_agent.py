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
# Single-pass structured extraction (response_schema) -> client.models.generate_content().
# This is not a multi-turn/tool-calling agent loop, so automatic function calling (which
# Google recommends only via Chat.send_message) is not a concern here.

class TailoredBullet(BaseModel):
    original_bullet: str = Field(description="The ground-truth bullet used in the source")
    tailored_bullet: str = Field(description="The rephrased bullet aligned with JD keywords")
    matched_keywords: List[str] = Field(description="ATS keywords incorporated into this bullet")


class TailoredResumeOutput(BaseModel):
    tailored_summary: str = Field(description="2-3 sentence elevator pitch aligned to the target JD")
    tailored_experience_bullets: List[TailoredBullet] = Field(description="List of verified and rephrased bullets")


class BulletReview(BaseModel):
    original_bullet: str = Field(description="The source bullet text")
    tailored_bullet: str = Field(description="The generated tailored bullet text")
    score: int = Field(description="Score out of 10 evaluating action verbs, metric retention, and JD relevance")
    feedback: str = Field(description="Specific actionable suggestion for improvement")


class ContentReviewOutput(BaseModel):
    overall_score: int = Field(description="Overall resume quality score (0 to 100)")
    meets_quality_bar: bool = Field(description="True if overall_score >= target quality threshold")
    summary_score: int = Field(description="Score out of 10 for the summary section")
    summary_feedback: str = Field(description="Critique and improvement ideas for the summary")
    bullet_reviews: List[BulletReview] = Field(description="Per-bullet quality evaluations")
    key_improvements: List[str] = Field(description="Top 3 actionable improvements for the next iteration")

@gemini_retry
@observe(name="Resume Tailoring: Writer Pass")
def generate_tailored_bullets(
    jd_analysis: JDAnalysis,
    master_profile: Dict[str, Any],
    audit_feedback: Optional[List[Dict[str, Any]]] = None,
    review_feedback: Optional[ContentReviewOutput] = None
) -> TailoredResumeOutput:
    critique_block = ""
    if audit_feedback:
        critique_block += f"\nCRITICAL SAFETY AUDIT FAILURES TO FIX:\n{json.dumps(audit_feedback, indent=2)}\nDO NOT hallucinate numbers or unverified tools.\n"
    if review_feedback:
        critique_block += f"\nQUALITY REVIEWER CRITIQUE (Previous Score: {review_feedback.overall_score}/100):\n"
        for imp in review_feedback.key_improvements:
            critique_block += f"- {imp}\n"
        critique_block += f"Summary Feedback: {review_feedback.summary_feedback}\n"

    prompt = f"""
    You are a Applied AI Engineering / Platform Engineering / Software Engineering Resume Specialist.
    Your task is to rephrase existing ground-truth achievements from the master profile to best align with the Target Job Analysis.

    {critique_block}
    CRITICAL SAFETY RULES:
    1. DO NOT invent new companies, metrics, numbers, or tools not present in the master profile.
    2. Maintain all quantitative metrics strictly as written (e.g., 98%, 60%, 40%, 90+ environments, 15+ clients).
    3. Re-frame technical phrasing toward applied AI, software engineering, and system reliability rather than pure data pipeline maintenance.

    TARGET JOB ANALYSIS:
    {jd_analysis.model_dump_json(indent=2)}

    GROUND TRUTH MASTER PROFILE:
    {json.dumps(master_profile, indent=2)}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            response_mime_type="application/json",
            response_schema=TailoredResumeOutput,
            temperature=0.2
        )
    )

    return TailoredResumeOutput.model_validate_json(response.text)

@gemini_retry
@observe(name="Resume Tailoring: Ground-Truth Audit")
def verify_output_safety(output: TailoredResumeOutput) -> Dict[str, Any]:
    audit_results = []
    has_violations = False

    for item in output.tailored_experience_bullets:
        check = verify_claim(item.tailored_bullet)
        audit_results.append({
            "bullet": item.tailored_bullet,
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
@observe(name="Resume Tailoring: Quality Reviewer")
def review_tailored_content_agent(
    jd_analysis: JDAnalysis,
    tailored_output: TailoredResumeOutput,
    quality_threshold: int = 85
) -> ContentReviewOutput:
    prompt = f"""
    You are an elite Technical Hiring Manager and Executive Resume Coach.
    Evaluate the tailored resume output against the target Job Description analysis.

    QUALITY EVALUATION CRITERIA:
    1. ATS & Keyword Alignment: Are key technical terms integrated naturally without keyword stuffing?
    2. Impact & Action Verbs: Do bullets start with strong engineering verbs (e.g., Engineered, Architected, Spearheaded, Accelerated)?
    3. Metrics & Outcome Focus: Are technical contributions clearly tied to measurable business/engineering outcomes?
    4. Conciseness & Tone: Is fluff eliminated? Is the summary self-sufficient, targeted, and compelling?

    Target Quality Threshold: {quality_threshold}/100.
    Set meets_quality_bar to true ONLY if overall_score >= {quality_threshold}.

    TARGET JOB ANALYSIS:
    {jd_analysis.model_dump_json(indent=2)}

    TAILORED RESUME OUTPUT TO EVALUATE:
    {tailored_output.model_dump_json(indent=2)}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1536),
            response_mime_type="application/json",
            response_schema=ContentReviewOutput,
            temperature=0.1
        )
    )

    review = ContentReviewOutput.model_validate_json(response.text)

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
@observe(name="Resume Tailoring Agent: Workflow Run")
def run_resume_tailoring_workflow(
    jd_text: str,
    max_iterations: int = 3,
    quality_threshold: int = 85
) -> Dict[str, Any]:
    print("🚀 [Resume Agent] Extracting JD Keywords & Requirements...")
    jd_analysis = analyze_job_description(jd_text)
    master_profile = _load_profile()

    audit_feedback = None
    review_feedback = None
    best_candidate = None
    highest_score = -1

    for iteration in range(1, max_iterations + 1):
        print(f"\n⚡ [Resume Iteration {iteration}/{max_iterations}] Tailoring Resume Content...")
        tailored_output = generate_tailored_bullets(
            jd_analysis, master_profile, audit_feedback, review_feedback
        )

        print("🛡️ [Resume Audit] Verifying Ground-Truth Metrics...")
        audit = verify_output_safety(tailored_output)

        print("🧐 [Resume Review] Evaluating Content Quality...")
        review = review_tailored_content_agent(
            jd_analysis, tailored_output, quality_threshold=quality_threshold
        )

        print(f"📊 Resume Score: {review.overall_score}/100 | Meets Bar ({quality_threshold}+): {review.meets_quality_bar} | Safety Passed: {audit['passed_audit']}")

        if audit["passed_audit"] and review.overall_score > highest_score:
            highest_score = review.overall_score
            best_candidate = {
                "jd_analysis": jd_analysis.model_dump(),
                "tailored_output": tailored_output.model_dump(),
                "audit_report": audit,
                "review_report": review.model_dump(),
                "iterations_taken": iteration,
                "early_stopped": False
            }

        if audit["passed_audit"] and review.meets_quality_bar:
            print(f"🎯 EARLY STOPPING: Resume score ({review.overall_score}/100) met threshold at iteration {iteration}!")
            best_candidate["early_stopped"] = True
            return best_candidate

        audit_feedback = audit["results"] if not audit["passed_audit"] else None
        review_feedback = review

    return best_candidate or {
        "jd_analysis": jd_analysis.model_dump(),
        "tailored_output": tailored_output.model_dump(),
        "audit_report": audit,
        "review_report": review.model_dump(),
        "iterations_taken": max_iterations,
        "early_stopped": False
    }