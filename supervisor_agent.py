import os, time
from dotenv import load_dotenv
from langfuse import observe

# Modular MCP Server Imports
from JobSearchMCP.linkedin_job_scraper_mcp import fetch_linkedin_jobs
from JobSearchMCP.linkedin_hiring_posts_mcp import search_hiring_manager_posts
from JobSearchMCP.indeed_scraper_mcp import fetch_indeed_jobs
from JobSearchMCP.wellfound_scraper_mcp import fetch_wellfound_jobs
from JobSearchMCP.glassdoor_scraper_mcp import fetch_glassdoor_jobs
from JobSearchMCP.ambitionbox_scraper_mcp import fetch_ambitionbox_jobs

# Modular Sub-Agent Imports
from SubAgents.hiring_manager_dm_agent import generate_hiring_manager_dm
from SubAgents.resume_tailoring_agent import run_resume_tailoring_workflow
from SubAgents.cover_letter_agent import run_cover_letter_workflow
from SubAgents.personal_ops_agent import log_tailored_application, generate_daily_standup_report

from OtherMCP.ground_truth_mcp import _load_profile
from OtherMCP.dispatch_mcp import generate_tailored_resume_docx

load_dotenv()

# ==============================================================================
# MASTER CONFIGURATION & SEARCH TARGETS
# ==============================================================================
MASTER_SEARCH_QUERIES = [
    "Forward Deployed Engineer", "Applied AI Engineer", "Software Engineer",
    "Platform Engineer", "Application Engineer", "AI Engineer",
    "Machine Learning Engineer", "Software Developer", "Python Developer"
]

MASTER_LOCATIONS = [
    "India", "Remote", "Bengaluru", "Bangalore", "Hyderabad", "Pune",
    "Delhi", "Gurgaon", "Noida", "Gurugram", "Mumbai", "New Delhi"
]

HIRING_PATTERNS = {
    "Pattern_A_Direct_Intent": ['"I\'m hiring"', '"looking for a"', '"open role on my team"'],
    "Pattern_B_Call_To_Action": ['"DM me"', '"send me your resume"', '"drop your portfolio"'],
    "Pattern_C_Team_Growth": ['"growing the team"', '"excited to announce"', '"just opened a req"']
}


# ------------------------------------------------------------------------------
# LINKEDIN QUERY LIMIT ADVISORY:
# LinkedIn enforces a hard cap of 5 Boolean operators (AND/OR/NOT) per search query.
# When calling `search_hiring_manager_posts`, we pass a MAX OF 3 ROLES AT A TIME.
# ------------------------------------------------------------------------------

@observe(name="Supervisor Agent: Modular MCP Multi-Agent Run")
def run_modular_executive_pipeline(generate_cover_letters: bool = True):
    print("==================================================")
    print("🤖 SUPERVISOR AGENT: MODULAR MCP ORCHESTRATION")
    print("==================================================")

    # Slicing top 3 target roles to respect the Boolean operator limit advisory
    active_roles_batch = MASTER_SEARCH_QUERIES[:3]

    # --------------------------------------------------------------------------
    # STEP 1: LINKEDIN HIRING MANAGER DISCOVERY & DM DRAFTING
    # --------------------------------------------------------------------------
    print("\n🎯 STEP 1: Discovering Hiring Managers on LinkedIn (Patterns A, B, C)...")
    leads = search_hiring_manager_posts(
        search_queries=active_roles_batch,
        locations=MASTER_LOCATIONS,
        pattern_type="Pattern_A_Direct_Intent",
        limit=5
    )

    profile = _load_profile()
    for idx, lead in enumerate(leads):
        company_name = lead.get('company') or 'Tech Company'
        print(f"\n📩 HIRING MANAGER OUTREACH DRAFT [{idx + 1}/{len(leads)}]: {lead['manager_name']} ({company_name})")
        dm = generate_hiring_manager_dm(lead, profile)
        print(
            f"--------------------------------------------------\n{dm}\n--------------------------------------------------")
        time.sleep(3)  # Short throttle between DM generation calls

    # --------------------------------------------------------------------------
    # STEP 2: MULTI-PLATFORM JOB BOARD INGESTION & DEDUPLICATION
    # --------------------------------------------------------------------------
    print("\n🌐 STEP 2: Ingesting Job Listings across Platform MCP Servers...")
    raw_jobs = []

    # LinkedIn: All 9 roles (Batched internally into 3-role Boolean chunks)
    raw_jobs.extend(
        fetch_linkedin_jobs(
            search_queries=MASTER_SEARCH_QUERIES,
            locations=MASTER_LOCATIONS,
            limit_per_query=2
        )
    )

    # Indeed: Top 3 roles
    raw_jobs.extend(
        fetch_indeed_jobs(
            search_queries=active_roles_batch,
            locations=["India", "Remote"],
            limit=2
        )
    )

    # Wellfound: Single focused role
    raw_jobs.extend(
        fetch_wellfound_jobs(
            search_queries=[active_roles_batch[1]],
            locations=MASTER_LOCATIONS,
            limit=1
        )
    )

    # Glassdoor: Top 3 roles
    raw_jobs.extend(
        fetch_glassdoor_jobs(
            search_queries=active_roles_batch,
            locations=MASTER_LOCATIONS,
            limit=1
        )
    )

    # AmbitionBox: Single focused role for India tech hubs
    raw_jobs.extend(
        fetch_ambitionbox_jobs(
            search_queries=[active_roles_batch[2]],
            locations=["Bengaluru"],
            limit=1
        )
    )

    # Cross-Platform Deduplication by normalized company + role signature
    seen_signatures = set()
    all_jobs = []
    for j in raw_jobs:
        sig = f"{j['company'].lower().strip()}:{j['role'].lower().strip()}"
        if sig not in seen_signatures:
            seen_signatures.add(sig)
            all_jobs.append(j)

    print(f"\n✅ Total Aggregated & Deduplicated Listings: {len(all_jobs)}")

    # --------------------------------------------------------------------------
    # STEP 3: WORKFLOW EXECUTION (RESUME + COVER LETTER + DISPATCH)
    # --------------------------------------------------------------------------
    for idx, job in enumerate(all_jobs):
        print(f"\n⚙️ PROCESSING [{idx + 1}/{len(all_jobs)}]: [{job['platform']}] {job['company']} - {job['role']}")

        # Throttle requests to respect Gemini RPM free rate limits
        if idx > 0:
            print("⏳ Throttling for 12 seconds to respect Gemini free rate limits...")
            time.sleep(12)

        # 1. Run Resume Tailoring Workflow Loop
        resume_res = run_resume_tailoring_workflow(
            jd_text=job["jd_text"],
            max_iterations=3,
            quality_threshold=85
        )

        # 2. Optionally Run Cover Letter Workflow Loop
        cover_letter_res = None
        if generate_cover_letters:
            cover_letter_res = run_cover_letter_workflow(
                jd_text=job["jd_text"],
                company_name=job["company"],
                max_iterations=3,
                quality_threshold=85
            )

        # 3. Document Dispatch & Application Logging
        if resume_res and resume_res.get("audit_report", {}).get("passed_audit"):
            tailored = resume_res["tailored_output"]

            doc_res = generate_tailored_resume_docx(
                company_name=job["company"],
                role_title=job["role"],
                tailored_summary=tailored["tailored_summary"],
                tailored_bullets=tailored["tailored_experience_bullets"]
            )

            log_tailored_application(
                company=f"{job['company']} ({job['platform']})",
                role=job["role"],
                tailored_summary=tailored["tailored_summary"],
                matched_keywords=resume_res["jd_analysis"]["ats_keywords"]
            )

            print(f"✅ Generated ATS Resume Local: {doc_res.get('local_path')}")
            print(f"☁️ Google Drive Link: {doc_res.get('drive_url')}")

            if cover_letter_res:
                print(f"✍️ Cover Letter Score: {cover_letter_res.get('review_report', {}).get('overall_score')}/100")

    # --------------------------------------------------------------------------
    # STEP 4: DAILY STANDUP REPORT
    # --------------------------------------------------------------------------
    print("\n" + generate_daily_standup_report())


if __name__ == "__main__":
    run_modular_executive_pipeline(generate_cover_letters=True)