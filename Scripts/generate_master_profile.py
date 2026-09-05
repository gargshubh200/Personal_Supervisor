import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
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

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
OUTPUT_PROFILE_PATH = Path(__file__).parent.parent / "master_profile.json"


# ------------------------------------------------------------------
# Master Profile Pydantic Schema
# ------------------------------------------------------------------

class PersonalInfo(BaseModel):
    name: str
    email: str
    linkedin: str
    github: str
    target_roles: List[str]


class VerifiedMetric(BaseModel):
    metric: str = Field(description="Short metric summary, e.g. '98% client onboarding reduction'")
    detail: str = Field(description="Context detail, e.g. 'From 15 days to <3 hours across 2 iterations'")


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
    verified_metrics: List[VerifiedMetric]
    skills: SkillsCategory
    education: Education


# ------------------------------------------------------------------
# Document Text Extraction
# ------------------------------------------------------------------

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
    You are an expert ATS data engineer. Extract the candidate's complete background into a structured Master Profile JSON.

    INSTRUCTIONS:
    1. Extract all quantitative metrics (percentages, numbers, time savings, environments) into 'verified_metrics'.
    2. Categorize all technical skills into the specified skill buckets.
    3. Retain exact numbers, dates, and achievements without summarizing away technical details.

    RAW RESUME TEXT:
    {raw_resume_text}
    """

    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
            response_schema=MasterProfileSchema,
            temperature=0.1
        )
    )

    profile_dict = json.loads(response.text)

    with open(OUTPUT_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile_dict, f, indent=2)

    print(f"✅ Master Profile successfully generated & saved to: {OUTPUT_PROFILE_PATH}")
    return profile_dict


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        generate_master_profile(sys.argv[1])
    else:
        print("Usage: python generate_master_profile.py <path_to_resume.pdf_or_.docx>")