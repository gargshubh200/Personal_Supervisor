from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from google.genai import types
from langfuse import observe, get_client

from SubAgents.gemini_common import client, gemini_retry, GEMINI_MODEL

# Single-pass structured extraction (response_schema) -> client.models.generate_content().
# This is not a multi-turn/tool-calling agent loop, so automatic function calling (which
# Google recommends only via Chat.send_message) is not a concern here.

class JDAnalysis(BaseModel):
    target_role: str = Field(description="Title of the role being applied for")
    required_tech_stack: List[str] = Field(description="Core tools and technologies mentioned in the JD")
    ats_keywords: List[str] = Field(description="Top 10 ATS keyword phrases to emphasize")
    key_responsibilities: List[str] = Field(description="Primary responsibilities expected by the employer")

    # --- Qualifications, consumed by matching_agent (fit score) ---
    required_qualifications: List[str] = Field(description="Must-have skills/experience the candidate is judged on, one per item; alternatives merged into ONE item ('One of: Python, Java, Scala')")
    preferred_qualifications: List[str] = Field(description="Nice-to-have / bonus / preferred skills and experience, one per item")

    # --- Listing-level signals, consumed by application_strategy_agent (priority) ---
    title_seniority: Literal["JUNIOR", "MID", "SENIOR", "LEAD_OR_ABOVE", "UNKNOWN"] = Field(description="Level implied by the title and requirements")
    listing_red_flags: List[str] = Field(description="Signals visible in the text that lower the value of applying, e.g. 'Posted 1 year ago', '200+ applicants', 'Reposted by a staffing agency/aggregator on behalf of an unnamed client'. Empty if none")

    # --- Eligibility facts, consumed by OtherMCP/eligibility_mcp.py (deterministic gate) ---
    is_job_posting: bool = Field(description="True only if the text is an actual job opening (not a blog post, article, discussion thread, or job-search listing page)")
    min_years_experience: Optional[float] = Field(description="Strictest minimum years of professional experience the posting asks for (lower bound of a range, e.g. '3-5 years' -> 3). Null if none stated")
    experience_evidence: Optional[str] = Field(description="Verbatim JD sentence stating the experience requirement, or null")
    employment_type: Literal["FULL_TIME", "CONTRACT", "PART_TIME", "INTERNSHIP", "UNKNOWN"] = Field(description="Employment type stated by the posting")
    work_arrangement: Literal["REMOTE", "HYBRID", "ONSITE", "UNKNOWN"] = Field(description="Where the work is performed")
    eligible_locations: List[str] = Field(description="Countries / regions / cities the candidate must live or work in (e.g. ['United States'], ['Bengaluru, India'], ['Worldwide'])")
    india_eligibility: Literal["ELIGIBLE", "NOT_ELIGIBLE", "UNCLEAR"] = Field(description="Can a candidate living in India take this job without relocating abroad?")
    location_evidence: Optional[str] = Field(description="Verbatim JD sentence(s) that determine india_eligibility, or null")


@gemini_retry
@observe(name="JD Analysis Engine")
def analyze_job_description(jd_text: str, listed_location: Optional[str] = None) -> JDAnalysis:
    """Extracts target skills, keywords, structural requirements and eligibility facts from the JD."""
    prompt = f"""
    You are an expert ATS parser and technical recruiter evaluating a Job Description.
    Extract the core target role, technical stack, top ATS keywords, and primary responsibilities.

    ALSO extract the eligibility facts below. Read the ENTIRE text — qualifications,
    location and employment terms are usually near the END.

    - is_job_posting: false for blog posts, articles, career-advice pages, forum/discussion threads,
      or pages listing many jobs. true only for a single real job opening.
    - min_years_experience: the strictest minimum years of professional experience the posting asks
      for. Use the lower bound of a range ("3-5 years" -> 3, "5+ years" -> 5). If a page header says
      one value and the qualifications section another, use the qualifications section. Count a
      "preferred" / "you may be a good fit if" requirement when it is the only experience statement.
      Null if no experience requirement is stated.
    - employment_type: CONTRACT for contractor / freelance / hourly / fixed-term roles,
      PART_TIME for part-time or <40h/week commitments, INTERNSHIP for internships,
      FULL_TIME for regular full-time employment, UNKNOWN if not stated.
    - work_arrangement: REMOTE, HYBRID, ONSITE, or UNKNOWN.
    - eligible_locations: where the hire must live/work, as stated by the posting.
    - india_eligibility:
        ELIGIBLE     -> the job is located in India (onsite/hybrid at an Indian city), OR it is remote
                        and explicitly open to India / APAC / Asia / worldwide / anywhere.
        NOT_ELIGIBLE -> onsite/hybrid only outside India; remote restricted to other countries/regions
                        (e.g. "US only", "must reside in the EU", "continental US"); or requires foreign
                        citizenship / work authorization / security clearance.
        UNCLEAR      -> remote with no stated country restriction and no India signal.
      Treat INR salaries, Indian cities, or Indian legal entities as India signals.
      Note: offering visa sponsorship to relocate abroad does NOT make a role ELIGIBLE.
    - location_evidence / experience_evidence: copy the exact sentence(s) you based the decision on.

    QUALIFICATIONS (what the candidate will be judged on — NOT what they will do on the job):
    - required_qualifications: skills / experience / knowledge listed as required, must-have, "what you
      bring", "you have", "minimum qualifications". If the JD has no explicit split, put the core
      skills it clearly expects here.
    - preferred_qualifications: items marked preferred, nice-to-have, bonus, plus, "ideally".
    - One item per distinct skill. Merge alternatives into ONE item ("6+ years in one of Python, Java,
      Scala, C++" -> "Production experience in one of: Python, Java, Scala, C++").
    - EXCLUDE from both lists: years-of-experience, location, work authorization, employment type and
      degree lines (handled separately), and day-to-day duties ("participate in on-call", "partner
      with design teams" belong in key_responsibilities).

    LISTING SIGNALS:
    - title_seniority: JUNIOR (junior / entry / associate / 0-2 yrs), MID (SDE II / Engineer II /
      unlabeled ~2-4 yrs), SENIOR (senior / Sr / SDE III / 5+ yrs), LEAD_OR_ABOVE (lead / staff /
      principal / manager), UNKNOWN.
    - listing_red_flags: only what is visible in the text — posting age over ~30 days, large
      applicant counts, staffing-agency / aggregator reposts for an unnamed client, roles that are
      mostly non-engineering. Empty list if none.

    LOCATION LISTED BY THE JOB SOURCE (may be missing or imprecise): {listed_location or "Unknown"}

    JOB DESCRIPTION:
    {jd_text}
    """

    response = client.models.generate_content(
        model=GEMINI_MODEL,
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
            "tech_count": str(len(analysis.required_tech_stack)),
            "min_years_experience": analysis.min_years_experience,
            "employment_type": analysis.employment_type,
            "india_eligibility": analysis.india_eligibility
        }
    )

    return analysis
