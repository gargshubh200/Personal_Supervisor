import os
from typing import List
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe, get_client

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
        model="gemini-3.5-flash",
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