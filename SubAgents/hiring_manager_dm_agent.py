import re
import json
from typing import Dict, Any
from google.genai import types
from langfuse import observe

from OtherMCP.ground_truth_mcp import _load_profile
from SubAgents.gemini_common import client, gemini_retry, GEMINI_MODEL

# Single-pass free-text generation (no tools/response_schema) -> client.models.generate_content().
# This is not a multi-turn/tool-calling agent loop, so automatic function calling (which
# Google recommends only via Chat.send_message) is not a concern here.

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

    # Ground-truth facts come from master_profile.json (regenerated from the latest
    # resume) — never hardcoded, so the pitch can't drift from the real profile.
    candidate_name = master_profile["personal_info"]["name"]
    candidate_facts = {
        "summary": master_profile.get("summary", ""),
        "experience": [
            {"role": e.get("role"), "company": e.get("company"), "period": e.get("period")}
            for e in master_profile.get("experience", [])
        ],
        "verified_accomplishments": [c.get("evidence") for c in master_profile.get("candidate_claims", []) if c.get("evidence")]
    }

    prompt = f"""
    You are an expert executive communication assistant. 
    Draft a hyper-concise (under 120 words), impactful LinkedIn DM or email to a hiring manager who posted an active opening.

    HIRING MANAGER POST DETAILS:
    Manager Name: {manager_lead.get('manager_name', '')}
    Title: {manager_lead.get('manager_title', '')}
    Matched Job Search Pattern: {manager_lead.get('matched_pattern', '')}
    Post Text Snippet: {manager_lead.get('post_text', '')}

    CANDIDATE GROUND-TRUTH FACTS ({candidate_name}, from master_profile.json):
    {json.dumps(candidate_facts, indent=2)}

    STRICT GUIDELINES:
    1. Do NOT sound spammy. Reference their specific post/hiring intent.
    2. Pitch 2 key accomplishments relevant to their opening, taken ONLY from the ground-truth facts above —
       keep every number exactly as written there and never introduce new metrics, tools or employers.
    3. End with a clear, low-friction call to action (e.g., "Open to a brief chat or sending over my tailored resume?").
    """

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            temperature=0.3
        )
    )
    return response.text.strip()