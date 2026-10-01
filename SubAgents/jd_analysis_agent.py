from typing import List
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

@gemini_retry
@observe(name="JD Analysis Engine")
def analyze_job_description(jd_text: str) -> JDAnalysis:
    """Extracts target skills, keywords, and structural requirements from the JD using Gemini 3.5."""
    prompt = f"""
    You are an expert ATS parser and technical recruiter evaluating a Job Description.
    Extract the core target role, technical stack, top ATS keywords, and primary responsibilities.

    JOB DESCRIPTION:
    {jd_text}
    """

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=512),
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
