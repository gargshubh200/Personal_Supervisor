import os
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe, get_client

# Import MCP verification logic directly
from OtherMCP.ground_truth_mcp import verify_claim, _load_profile

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai.errors import ClientError, ServerError

gemini_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    retry=retry_if_exception_type((ClientError, ServerError)),
    reraise=True
)

load_dotenv()

# Initialize Gemini Client
# client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
# NEW: GCP Vertex AI Client (Draws from your ₹28,694 GCP credits!)
client = genai.Client(
    vertexai=True,
    project="career-os-project",
    location="global"
)

# ------------------------------------------------------------------
# Structured Data Models — Job Analysis & Resume Tailoring
# ------------------------------------------------------------------

class JDAnalysis(BaseModel):
    target_role: str = Field(description="Title of the role being applied for")
    required_tech_stack: List[str] = Field(description="Core tools and technologies mentioned in the JD")
    ats_keywords: List[str] = Field(description="Top 10 ATS keyword phrases to emphasize")
    key_responsibilities: List[str] = Field(description="Primary responsibilities expected by the employer")


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


# ------------------------------------------------------------------
# Structured Data Models — Cover Letter Generation & Review
# ------------------------------------------------------------------

class CoverLetterOutput(BaseModel):
    salutation: str = Field(description="Professional greeting (e.g. 'Dear Hiring Team at [Company]')")
    opening_paragraph: str = Field(description="Engaging hook linking candidate core background to company & target role")
    body_paragraphs: List[str] = Field(description="2 structured paragraphs highlighting ground-truth metrics and engineering impact matching JD requirements")
    closing_paragraph: str = Field(description="Concise call-to-action and professional sign-off")
    full_cover_letter_text: str = Field(description="The complete, beautifully formatted cover letter in plain text")


class CoverLetterReviewOutput(BaseModel):
    overall_score: int = Field(description="Overall cover letter quality score (0 to 100)")
    meets_quality_bar: bool = Field(description="True if overall_score >= target quality threshold")
    relevance_score: int = Field(description="Score out of 10 for target JD and tech stack alignment")
    tone_and_clarity_score: int = Field(description="Score out of 10 for non-spammy, executive-ready tone")
    qualitative_critique: str = Field(description="Detailed qualitative feedback on narrative flow and impact")
    key_improvements: List[str] = Field(description="Top 3 actionable suggestions for refining the cover letter")


# ------------------------------------------------------------------
# Core Agent Pass 1: JD Analysis
# ------------------------------------------------------------------
@gemini_retry
@observe(name="Pass 1: JD Analysis")
def analyze_job_description(jd_text: str) -> JDAnalysis:
    """Extracts target skills, keywords, and structural requirements from the JD using Gemini 3.5 with thinking."""
    prompt = f"""
    You are an expert ATS parser and technical recruiter evaluating a Job Description.
    Extract the core target role, technical stack, top ATS keywords, and primary responsibilities.

    JOB DESCRIPTION:
    {jd_text}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
            response_schema=JDAnalysis,
            temperature=0.1
        )
    )

    analysis = JDAnalysis.model_validate_json(response.text)

    langfuse = get_client()
    langfuse.update_current_span(
        metadata={
            "target_role": analysis.target_role,
            "tech_count": str(len(analysis.required_tech_stack))
        }
    )

    return analysis


# ------------------------------------------------------------------
# Resume Tailoring Agents (Generation, Audit, Review)
# ------------------------------------------------------------------

@gemini_retry
@observe(name="Pass 2: Resume Tailoring & Alignment")
def generate_tailored_bullets(
    jd_analysis: JDAnalysis,
    master_profile: Dict[str, Any],
    audit_feedback: Optional[List[Dict[str, Any]]] = None,
    review_feedback: Optional[ContentReviewOutput] = None
) -> TailoredResumeOutput:
    """Selects and rephrases ground-truth achievements to match JD requirements, incorporating reviewer critique."""
    critique_block = ""
    if audit_feedback:
        critique_block += f"\nCRITICAL SAFETY AUDIT FAILURES TO FIX:\n{json.dumps(audit_feedback, indent=2)}\nDO NOT hallucinate numbers or unverified tools.\n"
    if review_feedback:
        critique_block += f"\nQUALITY REVIEWER CRITIQUE (Previous Score: {review_feedback.overall_score}/100):\n"
        for imp in review_feedback.key_improvements:
            critique_block += f"- {imp}\n"
        critique_block += f"Summary Feedback: {review_feedback.summary_feedback}\n"

    prompt = f"""
    You are a Forward Deployed / Applied AI Engineering Resume Specialist.
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
@observe(name="Pass 3: Resume Ground-Truth Verification Guardrail")
def verify_output_safety(output: TailoredResumeOutput) -> Dict[str, Any]:
    """Runs each tailored bullet through the MCP ground-truth verification guardrail."""
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
@observe(name="Pass 4: Resume Content Quality Review Agent")
def review_tailored_content_agent(
    jd_analysis: JDAnalysis,
    tailored_output: TailoredResumeOutput,
    quality_threshold: int = 85
) -> ContentReviewOutput:
    """Evaluates tailored summary and bullets for punchiness, action verbs, ATS alignment, and relevance."""
    prompt = f"""
    You are an elite Technical Hiring Manager and Executive Resume Coach.
    Evaluate the tailored resume output against the target Job Description analysis.

    QUALITY EVALUATION CRITERIA:
    1. ATS & Keyword Alignment: Are key technical terms integrated naturally without keyword stuffing?
    2. Impact & Action Verbs: Do bullets start with strong engineering verbs (e.g., Engineered, Architected, Spearheaded, Accelerated)?
    3. Metrics & Outcome Focus: Are technical contributions clearly tied to measurable business/engineering outcomes?
    4. Conciseness & Tone: Is fluff eliminated? Is the summary senior, targeted, and compelling?

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
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
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


# ------------------------------------------------------------------
# Cover Letter Agents (Writer, Safety Guardrail, Reviewer)
# ------------------------------------------------------------------

@gemini_retry
@observe(name="Cover Letter: Writer Agent")
def generate_cover_letter(
    jd_analysis: JDAnalysis,
    master_profile: Dict[str, Any],
    company_name: str,
    audit_feedback: Optional[List[Dict[str, Any]]] = None,
    review_feedback: Optional[CoverLetterReviewOutput] = None
) -> CoverLetterOutput:
    """Drafts a targeted, executive-ready cover letter grounded in candidate metrics and JD demands."""
    critique_block = ""
    if audit_feedback:
        critique_block += f"\nCRITICAL SAFETY AUDIT FAILURES TO FIX:\n{json.dumps(audit_feedback, indent=2)}\nDO NOT hallucinate metrics or tools.\n"
    if review_feedback:
        critique_block += f"\nCOVER LETTER CRITIQUE (Previous Score: {review_feedback.overall_score}/100):\n"
        for imp in review_feedback.key_improvements:
            critique_block += f"- {imp}\n"
        critique_block += f"Qualitative Feedback: {review_feedback.qualitative_critique}\n"

    prompt = f"""
    You are an Executive Cover Letter Writer for Forward Deployed & Applied AI Engineers.
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
    """Audits each paragraph of the generated cover letter against master profile facts."""
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
@observe(name="Cover Letter: Quality Review Agent")
def review_cover_letter_agent(
    jd_analysis: JDAnalysis,
    cover_letter: CoverLetterOutput,
    quality_threshold: int = 85
) -> CoverLetterReviewOutput:
    """Evaluates the cover letter for impact, executive engineering tone, relevance, and call-to-action strength."""
    prompt = f"""
    You are an Executive Technology Recruiter evaluating a Cover Letter.
    Critique the candidate's draft against the target Job Description analysis.

    EVALUATION STANDARDS:
    1. Hook & Positioning: Does the opening immediately demonstrate senior technical relevance?
    2. Specificity & Evidence: Are concrete, ground-truth engineering accomplishments cited cleanly?
    3. Tone & Alignment: Is the tone executive, crisp, and free of filler phrases like "I am writing to enthusiastically apply"?
    4. Call-to-Action: Is the closing concise and low-friction?

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
@observe(name="Cover Letter: Multi-Pass Execution Workflow")
def run_cover_letter_workflow(
    jd_analysis: JDAnalysis,
    master_profile: Dict[str, Any],
    company_name: str,
    max_iterations: int = 3,
    quality_threshold: int = 85
) -> Dict[str, Any]:
    """Runs a feedback-guided iteration loop for cover letter generation with early stopping."""
    audit_feedback = None
    review_feedback = None
    best_candidate = None
    highest_score = -1

    for iteration in range(1, max_iterations + 1):
        print(f"\n📝 [Cover Letter Iteration {iteration}/{max_iterations}] Drafting Content for {company_name}...")
        cl_output = generate_cover_letter(
            jd_analysis, master_profile, company_name, audit_feedback, review_feedback
        )

        print("🛡️ [Cover Letter Pass 2/3] Verifying Ground-Truth Claims...")
        audit = verify_cover_letter_safety(cl_output)

        print("🧐 [Cover Letter Pass 3/3] Evaluating Quality & Narrative Tone...")
        review = review_cover_letter_agent(
            jd_analysis, cl_output, quality_threshold=quality_threshold
        )

        print(f"📊 Cover Letter Score: {review.overall_score}/100 | Meets Bar ({quality_threshold}+): {review.meets_quality_bar} | Safety Audit Passed: {audit['passed_audit']}")

        if audit["passed_audit"] and review.overall_score > highest_score:
            highest_score = review.overall_score
            best_candidate = {
                "cover_letter": cl_output.model_dump(),
                "audit_report": audit,
                "review_report": review.model_dump(),
                "iterations_taken": iteration,
                "early_stopped": False
            }

        # EARLY STOPPING CONDITION
        if audit["passed_audit"] and review.meets_quality_bar:
            print(f"🎯 EARLY STOPPING TRIGGERED: Cover Letter score ({review.overall_score}/100) met target threshold at iteration {iteration}!")
            return {
                "cover_letter": cl_output.model_dump(),
                "audit_report": audit,
                "review_report": review.model_dump(),
                "iterations_taken": iteration,
                "early_stopped": True
            }

        audit_feedback = audit["results"] if not audit["passed_audit"] else None
        review_feedback = review

        if iteration < max_iterations:
            print("🔄 Refining cover letter with reviewer feedback...")

    if best_candidate:
        print(f"⚠️ Reached max cover letter iterations ({max_iterations}). Returning highest scoring draft (Score: {highest_score}/100).")
        return best_candidate

    return {
        "cover_letter": cl_output.model_dump(),
        "audit_report": audit,
        "review_report": review.model_dump(),
        "iterations_taken": max_iterations,
        "early_stopped": False
    }


# ------------------------------------------------------------------
# Central Orchestrator Pipeline
# ------------------------------------------------------------------

@observe(name="Career Agent: Unified Tailoring Pass")
def run_career_agent(
    jd_text: str,
    company_name: str = "Target Company",
    max_iterations: int = 3,
    quality_threshold: int = 85,
    include_cover_letter: bool = True
) -> Dict[str, Any]:
    """Full career agent pipeline: JD Analysis -> Tailored Resume Loop -> Cover Letter Workflow."""
    print("🚀 [Pass 1] Extracting JD Keywords & Requirements...")
    jd_analysis = analyze_job_description(jd_text)
    master_profile = _load_profile()

    audit_feedback = None
    review_feedback = None
    best_candidate = None
    highest_score = -1

    # --- RESUME TAILORING LOOP ---
    for iteration in range(1, max_iterations + 1):
        print(f"\n⚡ [Resume Iteration {iteration}/{max_iterations}] Tailoring Resume Content...")
        tailored_output = generate_tailored_bullets(
            jd_analysis, master_profile, audit_feedback, review_feedback
        )

        print("🛡️ [Resume Audit] Running Ground-Truth Verification Guardrail...")
        audit = verify_output_safety(tailored_output)

        print("🧐 [Resume Review] Reviewing Content Quality...")
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
            print(f"🎯 EARLY STOPPING: Resume score ({review.overall_score}/100) met target threshold at iteration {iteration}!")
            best_candidate = {
                "jd_analysis": jd_analysis.model_dump(),
                "tailored_output": tailored_output.model_dump(),
                "audit_report": audit,
                "review_report": review.model_dump(),
                "iterations_taken": iteration,
                "early_stopped": True
            }
            break

        audit_feedback = audit["results"] if not audit["passed_audit"] else None
        review_feedback = review

    # --- OPTIONAL COVER LETTER WORKFLOW ---
    cover_letter_res = None
    if include_cover_letter:
        print("\n" + "=" * 50)
        print("✍️ GENERATING TAILORED COVER LETTER")
        print("=" * 50)
        cover_letter_res = run_cover_letter_workflow(
            jd_analysis=jd_analysis,
            master_profile=master_profile,
            company_name=company_name,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold
        )

    return {
        "jd_analysis": best_candidate["jd_analysis"],
        "tailored_output": best_candidate["tailored_output"],
        "audit_report": best_candidate["audit_report"],
        "review_report": best_candidate["review_report"],
        "cover_letter_result": cover_letter_res,
        "iterations_taken": best_candidate["iterations_taken"],
        "early_stopped": best_candidate["early_stopped"]
    }

@gemini_retry
@observe(name="Career Agent: Draft Hiring Manager Cold Outreach DM")
def generate_hiring_manager_dm(manager_lead: Dict[str, Any], master_profile: Dict[str, Any]) -> str:
    """Drafts a concise direct message tailored to a hiring manager's post using Gemini 3.5 with thinking."""
    prompt = f"""
    You are an expert executive communication assistant. 
    Draft a hyper-concise (under 120 words), impactful LinkedIn DM or email to a hiring manager who posted an active opening.

    HIRING MANAGER POST DETAILS:
    Manager Name: {manager_lead['manager_name']}
    Title: {manager_lead['manager_title']}
    Matched Job Search Pattern: {manager_lead['matched_pattern']}
    Post Text Snippet: {manager_lead['post_text']}

    CANDIDATE GROUND-TRUTH FACTS (Sahil Garg):
    • Software Engineer at o9 Solutions with 2.5 years of experience building applied AI systems, enterprise backends, and FDE tooling.
    • Engineered centralized monitoring ETL architecture across 90+ enterprise client environments, achieving a 98% onboarding reduction (15 days to <3 hours).
    • Architected a grammar-driven log obfuscation engine (ANTLR) that unblocked compliance and security requirements across 15+ enterprise clients.
    • Developed a PageRank graph-based entity ranking engine to extract structured semantic context layers from unstructured enterprise metadata.
    • Built a skills-based planning agent utilizing runtime semantic retrieval, driving a 60% reduction in query resolution time.
    • Specialized in Forward Deployed Engineering, Applied AI, agentic LLM workflows, and high-performance Python backends.

    STRICT GUIDELINES:
    1. Do NOT sound spammy. Reference their specific post/hiring intent.
    2. Pitch 2 key ground-truth accomplishments relevant to their opening.
    3. End with a clear, low-friction call to action (e.g., "Open to a brief chat or sending over my tailored resume?").
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.3
        )
    )
    return response.text.strip()