import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from docx import Document
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

# Google API Client Imports
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

mcp = MCPServer("DispatchServer")
langfuse = get_client()
load_dotenv()

OUTPUT_DIR = Path(__file__).parent / "output_resumes"
OUTPUT_DIR.mkdir(exist_ok=True)

DATA_PATH = Path(__file__).parent.parent / "master_profile.json"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _load_profile() -> Dict[str, Any]:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def set_spacing(paragraph, space_before=0, space_after=2, line_spacing=1.15):
    p_format = paragraph.paragraph_format
    p_format.space_before = Pt(space_before)
    p_format.space_after = Pt(space_after)
    p_format.line_spacing = line_spacing


# ------------------------------------------------------------------
# Google Drive Authentication & Upload Service
# ------------------------------------------------------------------

def _get_drive_service():
    """
    Authenticates with Google Drive API.
    Supports either Service Account (service_account.json) or OAuth (credentials.json / token.json).
    """
    creds = None
    service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
    token_path = Path(__file__).parent / "token.json"
    credentials_path = Path(__file__).parent / "credentials.json"

    # Option A: Service Account (Best for autonomous backend agents)
    if os.path.exists(service_account_path):
        creds = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=DRIVE_SCOPES
        )
    # Option B: OAuth User Credentials (Fallback)
    elif token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), DRIVE_SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
    elif credentials_path.exists():
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), DRIVE_SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    if not creds:
        return None

    return build("drive", "v3", credentials=creds)


def _upload_to_google_drive(file_path: Path, convert_to_gdoc: bool = True) -> Dict[str, str]:
    """
    Uploads a local .docx file to Google Drive.
    Optionally converts it directly into Google Docs format.
    """
    try:
        service = _get_drive_service()
        if not service:
            print("⚠️ Google Drive credentials not found. Skipping Drive upload.")
            return {"status": "skipped", "drive_url": None}

        folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        file_metadata: Dict[str, Any] = {"name": file_path.name}

        if folder_id:
            file_metadata["parents"] = [folder_id]

        # Convert to Google Docs format for instant online viewing/editing
        if convert_to_gdoc:
            file_metadata["mimeType"] = "application/vnd.google-apps.document"

        media = MediaFileUpload(
            str(file_path),
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            resumable=True
        )

        drive_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink"
        ).execute()

        file_id = drive_file.get("id")
        web_link = drive_file.get("webViewLink")

        print(f"☁️ Google Drive Upload Successful! File ID: {file_id}")
        return {
            "status": "success",
            "file_id": file_id,
            "drive_url": web_link
        }

    except Exception as e:
        print(f"❌ Google Drive Upload Error: {str(e)}")
        return {"status": "error", "error": str(e), "drive_url": None}


# ------------------------------------------------------------------
# Resume Generation Tool
# ------------------------------------------------------------------

@observe(name="Dispatch: Generate ATS Resume Document")
@mcp.tool()
def generate_tailored_resume_docx(
    company_name: str,
    role_title: str,
    tailored_summary: str,
    tailored_bullets: List[Dict[str, Any]]
) -> Dict[str, str]:
    """
    Generates a clean, ATS-friendly .docx resume, saves it locally in output_resumes/,
    and uploads it to Google Drive. Returns local path and Google Drive URL.
    """
    profile = _load_profile()
    info = profile["personal_info"]

    doc = Document()

    # 1. Page Margins (0.5 inch top/bottom, 0.6 left/right)
    for section in doc.sections:
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin = Inches(0.6)
        section.right_margin = Inches(0.6)

    # 2. Header Block (Name + Contact Info)
    name_p = doc.add_paragraph()
    set_spacing(name_p, space_after=2)
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_name = name_p.add_run(info["name"].upper())
    run_name.font.name = "Arial"
    run_name.font.size = Pt(16)
    run_name.font.bold = True

    contact_p = doc.add_paragraph()
    set_spacing(contact_p, space_after=10)
    contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    contact_text = f"+91 9109268883 | {info['email']} | {info['linkedin']} | {info['github']}"
    run_contact = contact_p.add_run(contact_text)
    run_contact.font.name = "Arial"
    run_contact.font.size = Pt(9.5)

    def add_section_heading(title: str):
        p = doc.add_paragraph()
        set_spacing(p, space_before=8, space_after=4)
        run = p.add_run(title.upper())
        run.font.name = "Arial"
        run.font.size = Pt(11)
        run.font.bold = True
        return p

    # 3. Summary Section
    add_section_heading("Summary")
    sum_p = doc.add_paragraph()
    set_spacing(sum_p, space_after=8)
    sum_run = sum_p.add_run(tailored_summary)
    sum_run.font.name = "Arial"
    sum_run.font.size = Pt(10)

    # 4. Experience Section
    add_section_heading("Experience")
    bullet_map = {b.get("original_bullet", ""): b.get("tailored_bullet", "") for b in tailored_bullets}

    for exp in profile.get("experience", []):
        job_header = doc.add_paragraph()
        set_spacing(job_header, space_before=4, space_after=2)

        role_run = job_header.add_run(f"{exp['role']} — {exp['company']}")
        role_run.font.name = "Arial"
        role_run.font.size = Pt(10.5)
        role_run.font.bold = True

        details_text = f" | {exp['period']} | {exp.get('location', '')}"
        details_run = job_header.add_run(details_text)
        details_run.font.name = "Arial"
        details_run.font.size = Pt(10)

        for project in exp.get("projects", []):
            proj_p = doc.add_paragraph()
            set_spacing(proj_p, space_before=2, space_after=2)
            proj_run = proj_p.add_run(project["name"])
            proj_run.font.name = "Arial"
            proj_run.font.size = Pt(10)
            proj_run.font.bold = True
            proj_run.font.italic = True

            for orig_bullet in project.get("achievements", []):
                bullet_text = bullet_map.get(orig_bullet, orig_bullet)
                bp = doc.add_paragraph(style='List Bullet')
                set_spacing(bp, space_before=0, space_after=2)
                b_run = bp.add_run(bullet_text)
                b_run.font.name = "Arial"
                b_run.font.size = Pt(9.5)

    # 5. Skills Section
    add_section_heading("Skills")
    skills = profile.get("skills", {})

    for category, skill_list in skills.items():
        sk_p = doc.add_paragraph()
        set_spacing(sk_p, space_before=0, space_after=2)
        cat_formatted = category.replace("_", " ").title()

        cat_run = sk_p.add_run(f"{cat_formatted}: ")
        cat_run.font.name = "Arial"
        cat_run.font.size = Pt(9.5)
        cat_run.font.bold = True

        list_run = sk_p.add_run(", ".join(skill_list))
        list_run.font.name = "Arial"
        list_run.font.size = Pt(9.5)

    # 6. Education Section
    add_section_heading("Education")
    edu_p = doc.add_paragraph()
    set_spacing(edu_p, space_before=2, space_after=2)
    edu_run = edu_p.add_run(profile["education"])
    edu_run.font.name = "Arial"
    edu_run.font.size = Pt(9.5)

    # Save to local file
    safe_company = "".join([c for c in company_name if c.isalnum() or c in (" ", "_")]).strip()
    safe_role = "".join([c for c in role_title if c.isalnum() or c in (" ", "_")]).strip()
    file_name = f"ATS_Resume_Sahil_Garg_{safe_company}_{safe_role}.docx".replace(" ", "_")
    output_path = OUTPUT_DIR / file_name

    doc.save(str(output_path))
    print(f"📄 Local Resume Saved: {output_path}")

    # 7. Upload to Google Drive
    drive_result = _upload_to_google_drive(output_path, convert_to_gdoc=True)

    # Log telemetry
    langfuse.update_current_span(
        metadata={
            "company_name": company_name,
            "role_title": role_title,
            "local_path": str(output_path),
            "drive_status": drive_result["status"],
            "drive_url": str(drive_result.get("drive_url"))
        }
    )

    return {
        "local_path": str(output_path),
        "drive_url": drive_result.get("drive_url") or "Upload skipped or failed",
        "file_id": drive_result.get("file_id", "")
    }


if __name__ == "__main__":
    sample_summary = "Software Engineer specializing in applied AI systems, custom backend tooling, and scalable enterprise platform software."
    sample_bullets = [
        {
            "original_bullet": "Engineered centralized ETL architecture as sole engineer, resolving production reliability failures affecting 90+ enterprise client environments.",
            "tailored_bullet": "Engineered centralized backend systems as sole engineer, resolving production reliability failures across 90+ enterprise client environments."
        }
    ]
    res = generate_tailored_resume_docx("Palantir", "Forward Deployed Engineer", sample_summary, sample_bullets)
    print(f"Result: {res}")