import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Optional document parsing imports
try:
    from docx import Document
except ImportError:
    Document = None

try:
    import pypdf
except ImportError:
    pypdf = None

load_dotenv()

client = genai.Client(
    vertexai=True,
    project="career-os-project",
    location="global"
)
# Single-pass structured extraction (response_schema) -> client.models.generate_content().
# This is not a multi-turn/tool-calling agent loop, so automatic function calling (which
# Google recommends only via Chat.send_message) is not a concern here.
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_PROFILE_PATH = REPO_ROOT / "master_profile.json"
CANDIDATE_RESUME_DIR = REPO_ROOT / "CandidateResumeDoc"


# ------------------------------------------------------------------
# Master Profile & Provenance Pydantic Schema
# ------------------------------------------------------------------

class PersonalInfo(BaseModel):
    name: str
    email: str
    linkedin: str
    github: str
    target_roles: List[str]


class MetricValue(BaseModel):
    raw_value: str = Field(description="Primary metric or delta, e.g. '98%', '90+', '60%'")
    before: Optional[str] = Field(default=None, description="Baseline metric state if specified, e.g. '15 days'")
    after: Optional[str] = Field(default=None, description="Improved metric state if specified, e.g. '<3 hours'")


class CandidateClaim(BaseModel):
    metric: str = Field(description="Short title of claimed metric, e.g. 'Client onboarding reduction'")
    value: MetricValue = Field(description="Deconstructed numerical or quantitative values")
    source: str = Field(default="resume", description="Document origin of claim, e.g. 'resume', 'linkedin'")
    evidence: str = Field(description="Verbatim text/bullet excerpt from source material supporting this claim")
    verification_status: Literal["candidate_claimed", "candidate_verified", "third_party_verified"] = Field(
        default="candidate_claimed",
        description="Provenance level: 'candidate_claimed' (raw extraction), 'candidate_verified' (user confirmed), 'third_party_verified' (audit proof)"
    )


class Project(BaseModel):
    name: str
    achievements: List[str]


class ExperienceItem(BaseModel):
    company: str
    role: str
    period: str
    location: str
    projects: Optional[List[Project]] = None
    achievements: Optional[List[str]] = None


class SkillsCategory(BaseModel):
    languages_and_tools: List[str]
    ai_and_agentic: List[str]
    llm_concepts: List[str]
    data_and_orchestration: List[str]
    cloud_and_infra: List[str]
    specialized: List[str]


class Education(BaseModel):
    institution: str
    degree: str
    period: str
    achievements: List[str]


class MasterProfileSchema(BaseModel):
    personal_info: PersonalInfo
    summary: str
    experience: List[ExperienceItem]
    candidate_claims: List[CandidateClaim] = Field(description="Itemized quantitative claims extracted from source documents")
    skills: SkillsCategory
    education: Education


# ------------------------------------------------------------------
# Document Text Extraction
# ------------------------------------------------------------------

def find_latest_resume_in_candidate_folder(folder: Path = CANDIDATE_RESUME_DIR) -> Optional[Path]:
    """
    Locates the most recently modified supported resume file (.docx/.pdf) inside
    the CandidateResumeDoc folder. Returns None if the folder doesn't exist or is empty.
    """
    if not folder.exists():
        return None

    candidates = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in (".docx", ".pdf") and not f.name.startswith("~$")
    ]
    if not candidates:
        return None

    return max(candidates, key=lambda f: f.stat().st_mtime)


def extract_text_from_file(file_path: Path) -> str:
    ext = file_path.suffix.lower()

    if ext == ".docx":
        if not Document:
            raise ImportError("Please run `pip install python-docx` to read .docx files.")
        doc = Document(str(file_path))
        return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    elif ext == ".pdf":
        if not pypdf:
            raise ImportError("Please run `pip install pypdf` to read .pdf files.")
        reader = pypdf.PdfReader(str(file_path))
        text = []
        for page in reader.pages:
            extracted = page.extract_text()
            if extracted:
                text.append(extracted)
        return "\n".join(text)

    else:
        raise ValueError(f"Unsupported file format: {ext}. Supported formats: .pdf, .docx")


# ------------------------------------------------------------------
# Profile Generation Engine
# ------------------------------------------------------------------

def generate_master_profile(resume_file_path: str) -> Dict[str, Any]:
    file_path = Path(resume_file_path)
    print(f"📄 Extracting text from: {file_path.name}...")
    raw_resume_text = extract_text_from_file(file_path)

    print("🧠 Parsing structured profile with Gemini 3.5...")
    prompt = f"""
    You are an expert ATS data engineer and provenance auditor. Extract the candidate's complete background into a structured Master Profile JSON.

    PROVENANCE & EXTRACTION DIRECTIVES:
    1. Extract all quantitative metrics, numbers, environments, and time savings into 'candidate_claims'.
    2. Set 'source' to 'resume'.
    3. Copy the exact verbatim bullet/sentence from the raw text into 'evidence'.
    4. Set 'verification_status' strictly to 'candidate_claimed' (since assertions extracted from a resume are unverified until audited).
    5. Deconstruct metric deltas into 'raw_value', 'before', and 'after' fields wherever baseline/improved states exist.
    6. Categorize all technical skills into the specified skill categories.
    7. Retain exact numbers, dates, and achievements without summarizing away technical details.

    RAW RESUME TEXT:
    {raw_resume_text}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            response_mime_type="application/json",
            response_schema=MasterProfileSchema,
            temperature=0.1
        )
    )

    profile_dict = json.loads(response.text)

    with open(OUTPUT_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile_dict, f, indent=2)

    print(f"✅ Master Profile with candidate claims successfully generated & saved to: {OUTPUT_PROFILE_PATH}")
    return profile_dict


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        generate_master_profile(sys.argv[1])
    else:
        auto_resume = find_latest_resume_in_candidate_folder()
        if auto_resume:
            generate_master_profile(str(auto_resume))
        else:
            print("Usage: python generate_master_profile.py <path_to_resume.pdf_or_.docx>")
            print(f"(No resume found in {CANDIDATE_RESUME_DIR} either.)")
