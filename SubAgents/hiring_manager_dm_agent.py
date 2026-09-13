import os
import re
from typing import Dict, Any
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langfuse import observe

from OtherMCP.ground_truth_mcp import _load_profile

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

PROHIBITED_NAME_WORDS = {"jobs", "job", "hiring", "recruiter", "careers", "followers", "subscribers", "board", "hub",
                         "agency"}


def _validate_manager_lead(manager_lead: Dict[str, Any]) -> bool:
    """Refuses execution if manager_name or title belongs to a job aggregator/page."""
    name = manager_lead.get("manager_name", "").strip()
    title = manager_lead.get("manager_title", "").strip()

    if not name or name == "Hiring Manager":
        return False

    name_lower = name.lower()
    title_lower = title.lower()

    # Reject non-human name patterns
    if any(word in name_lower.split() for word in PROHIBITED_NAME_WORDS):
        return False

    if any(char.isdigit() for char in name):
        return False

    # Name must have at least first name
    if len(name.split()) < 1:
        return False

    # Title must not be a follower count page
    if any(bad in title_lower for bad in ["follower", "followers", "subscriber", "job board"]):
        return False

    return True


@gemini_retry
@observe(name="Hiring Manager DM Agent: Draft Outreach")
def generate_hiring_manager_dm(
        manager_lead: Dict[str, Any],
        master_profile: Dict[str, Any] = None
) -> str:
    """Drafts a concise direct message tailored to a hiring manager's post using Gemini 3.5."""

    # Validation Guard: Refuse drafting if lead is not a real human hiring manager
    if not _validate_manager_lead(manager_lead):
        name = manager_lead.get("manager_name", "Unknown Target")
        print(f"⚠️ DM AGENT REFUSAL: Target '{name}' is a job board or non-human entity. Skipping outreach generation.")
        return f"[OUTREACH SKIPPED: Target '{name}' is not a verified individual hiring manager]"

    if not master_profile:
        master_profile = _load_profile()

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