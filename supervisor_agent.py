import os
from dotenv import load_dotenv
from langfuse import observe

# Modular MCP Server Imports
from JobSearchMCP.ats_direct_mcp import fetch_ats_direct_jobs
from JobSearchMCP.ats_google_dork_mcp import search_ats_via_google_dork
from JobSearchMCP.linkedin_job_scraper_mcp import fetch_linkedin_jobs
from JobSearchMCP.linkedin_hiring_posts_mcp import search_hiring_manager_posts
from JobSearchMCP.indeed_scraper_mcp import fetch_indeed_jobs
from JobSearchMCP.wellfound_scraper_mcp import fetch_wellfound_jobs
from JobSearchMCP.glassdoor_scraper_mcp import fetch_glassdoor_jobs
from JobSearchMCP.ambitionbox_scraper_mcp import fetch_ambitionbox_jobs

# Modular Sub-Agent Imports
from SubAgents.hiring_manager_dm_agent import generate_hiring_manager_dm
from SubAgents.jd_analysis_agent import analyze_job_description
from SubAgents.matching_agent import evaluate_candidate_match
from SubAgents.application_strategy_agent import determine_application_strategy
from SubAgents.resume_tailoring_agent import run_resume_tailoring_workflow
from SubAgents.cover_letter_agent import run_cover_letter_workflow
from SubAgents.personal_ops_agent import generate_daily_standup_report

from OtherMCP.ground_truth_mcp import _load_profile
from OtherMCP.dispatch_mcp import generate_tailored_resume_docx

# Phase 3 & 4 Imports
from db_manager import DatabaseManager
from OtherMCP.notification_mcp import send_executive_briefing

load_dotenv()

# ==============================================================================
# FEATURE TOGGLES & COST CONTROL CONFIGURATION
# ==============================================================================
MCP_TOGGLES = {
    "linkedin_hiring_posts": True,  # High-value hiring manager outreach
    "ats_direct": True,  # Free public ATS APIs (Greenhouse/Lever)
    "ats_google_dork": True,  # Free dorking via Serper/Tavily
    "linkedin_jobs": False,  # Optional: Turn on when needed
    "indeed": False,  # Optional
    "wellfound": False,  # Optional
    "glassdoor": False,  # Optional
    "ambitionbox": False  # Optional
}

# ==============================================================================
# MASTER TARGET ROLES & STRATEGY CONSTRAINTS
# ==============================================================================
MASTER_SEARCH_QUERIES = [
    "Software Engineer",
    "Applied AI Engineer",
    "Platform Engineer",
    "AI Infrastructure Engineer",
    "Backend Engineer",
    "Data Engineer",
    "Python Developer"
]

MASTER_LOCATIONS = [
    "India", "Remote", "Bengaluru", "Bangalore", "Hyderabad", "Pune",
    "Delhi", "Gurgaon", "Noida", "Gurugram", "Mumbai", "New Delhi"
]

CAREER_STRATEGY_CONSTRAINTS = {
    "excluded_companies": [
        "o9 solutions",
        "o9solutions",
        "o9"
    ],
    "excluded_role_keywords": [
        "forward deployed",
        "fde",
        "solutions engineer",
        "support engineer",
        "salesforce",
        "apex",
        "manager",
        "director",
        "vp",
        "presales",
        "sales engineer"
    ],
    "preferred_role_keywords": [
        "software engineer",
        "applied ai",
        "platform engineer",
        "ai infrastructure",
        "backend engineer",
        "data engineer"
    ]
}


def is_location_eligible(job_location: str, allowed_locations: list) -> bool:
    """Verifies if a scraped job location matches target geographic constraints."""
    if not job_location or job_location.upper() == "N/A":
        return True  # Retain unassigned locations for LLM evaluation

    loc_lower = job_location.lower()
    return any(target.lower() in loc_lower for target in allowed_locations) or "remote" in loc_lower


@observe(name="Supervisor Agent: Modular MCP Multi-Agent Run")
def run_modular_executive_pipeline(generate_cover_letters: bool = True):
    print("==================================================")
    print("🤖 SUPERVISOR AGENT: MODULAR MCP ORCHESTRATION")
    print("==================================================")

    db = DatabaseManager()
    active_roles_batch = MASTER_SEARCH_QUERIES[:3]

    # --------------------------------------------------------------------------
    # STEP 1: LINKEDIN HIRING MANAGER DISCOVERY & DM DRAFTING
    # --------------------------------------------------------------------------
    hiring_manager_dms = {}
    if MCP_TOGGLES.get("linkedin_hiring_posts"):
        print("\n🎯 STEP 1: Discovering Hiring Managers on LinkedIn...")
        try:
            leads = search_hiring_manager_posts(
                search_queries=active_roles_batch,
                locations=MASTER_LOCATIONS,
                pattern_type="Pattern_A_Direct_Intent",
                limit=5
            )

            profile = _load_profile()
            for idx, lead in enumerate(leads):
                company_name = lead.get('company') or 'Tech Company'
                print(
                    f"📩 Drafting DM [{idx + 1}/{len(leads)}]: {lead.get('manager_name', 'Hiring Manager')} ({company_name})")
                dm = generate_hiring_manager_dm(lead, profile)
                if not dm.startswith("[OUTREACH SKIPPED"):
                    hiring_manager_dms[company_name.lower().strip()] = dm
        except Exception as e:
            print(f"⚠️ Warning during hiring manager discovery: {str(e)}")
    else:
        print("\n⏭️ STEP 1 SKIPPED: 'linkedin_hiring_posts' MCP is disabled in config.")

    # --------------------------------------------------------------------------
    # STEP 2: MULTI-PLATFORM JOB BOARD & ATS INGESTION
    # --------------------------------------------------------------------------
    print("\n🌐 STEP 2: Ingesting Job Listings across Enabled Platform MCPs...")
    raw_jobs = []

    if MCP_TOGGLES.get("ats_direct"):
        print("  ↳ Fetching Direct ATS APIs...")
        raw_jobs.extend(fetch_ats_direct_jobs(limit=5))

    if MCP_TOGGLES.get("ats_google_dork"):
        print("  ↳ Executing ATS Google Dorking...")
        raw_jobs.extend(search_ats_via_google_dork(limit=5))

    if MCP_TOGGLES.get("linkedin_jobs"):
        print("  ↳ Scraping LinkedIn Jobs...")
        raw_jobs.extend(
            fetch_linkedin_jobs(search_queries=MASTER_SEARCH_QUERIES, locations=MASTER_LOCATIONS, limit_per_query=2))

    if MCP_TOGGLES.get("indeed"):
        print("  ↳ Scraping Indeed Jobs...")
        raw_jobs.extend(fetch_indeed_jobs(search_queries=active_roles_batch, locations=["India", "Remote"], limit=2))

    if MCP_TOGGLES.get("wellfound"):
        print("  ↳ Scraping Wellfound Jobs...")
        raw_jobs.extend(
            fetch_wellfound_jobs(search_queries=[active_roles_batch[1]], locations=MASTER_LOCATIONS, limit=1))

    if MCP_TOGGLES.get("glassdoor"):
        print("  ↳ Scraping Glassdoor Jobs...")
        raw_jobs.extend(fetch_glassdoor_jobs(search_queries=active_roles_batch, locations=MASTER_LOCATIONS, limit=1))

    if MCP_TOGGLES.get("ambitionbox"):
        print("  ↳ Scraping AmbitionBox Jobs...")
        raw_jobs.extend(
            fetch_ambitionbox_jobs(search_queries=[active_roles_batch[2]], locations=["Bengaluru"], limit=1))

    # Filtering & Deduplication Logic
    seen_signatures = set()
    all_jobs = []
    skipped_count = 0
    excluded_company_count = 0
    foreign_loc_count = 0

    for j in raw_jobs:
        company = j.get('company', '').strip()
        role = j.get('role', '').strip()
        location = j.get('location', 'India').strip()
        sig = f"{company.lower()}:{role.lower()}"

        # 1. Filter out Current Employer
        if any(ex_comp in company.lower() for ex_comp in CAREER_STRATEGY_CONSTRAINTS["excluded_companies"]):
            excluded_company_count += 1
            continue

        # 2. Filter out Ineligible Geographic Locations
        if not is_location_eligible(location, MASTER_LOCATIONS):
            foreign_loc_count += 1
            continue

        # 3. In-Memory Deduplication
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)

        # 4. Firestore Historical Deduplication
        if db.is_job_processed(company, role):
            skipped_count += 1
            continue

        all_jobs.append(j)

    print(f"✅ Deduplication Summary: {len(all_jobs)} eligible job leads retained.")
    print(
        f"   ↳ Filtered Out: {skipped_count} previously in DB | {foreign_loc_count} foreign locations | {excluded_company_count} current employer instances.")

    # --------------------------------------------------------------------------
    # STEP 3: MULTI-AGENT PROCESSING & GATED WORKFLOW EXECUTION
    # --------------------------------------------------------------------------
    for idx, job in enumerate(all_jobs):
        company = job['company']
        role = job['role']
        location = job.get('location', 'India')
        job_url = job.get('job_url', '')

        print(f"\n⚙️ PROCESSING [{idx + 1}/{len(all_jobs)}]: [{job['platform']}] {company} - {role}")

        # 1. Parse Job Description Features
        print("🧠 [Agent 1/5] Extracting JD Keywords & Stack Requirements...")
        jd_analysis = analyze_job_description(job["jd_text"])

        # 2. Run Deterministic Grounded Match Evaluation
        print("📊 [Agent 2/5] Evaluating Candidate Requirement Match...")
        matching_report = evaluate_candidate_match(jd_analysis=jd_analysis, threshold=60.0)
        match_score = int(matching_report.deterministic_score)
        print(f"   ↳ Match Score: {match_score}% | {matching_report.summary}")

        # 3. Formulate Application Strategy (Enforcing Constraints)
        print("🎯 [Agent 3/5] Determining Application Strategy...")
        strategy = determine_application_strategy(
            company_name=company,
            role_title=role,
            jd_analysis=jd_analysis,
            matching_report=matching_report,
            career_constraints=CAREER_STRATEGY_CONSTRAINTS
        )
        print(f"   ↳ Decision: {strategy.decision} | Priority: {strategy.priority} | Reason: {strategy.reasoning}")

        drive_resume_link = None
        drive_cover_letter_link = None

        # ----------------------------------------------------------------------
        # CONDITIONAL GATEWAY: Tailoring & Document Generation
        # ----------------------------------------------------------------------
        if strategy.decision == "APPLY" and strategy.should_tailor_resume == "YES":
            print(f"🚀 Moving forward with resume tailoring & document dispatch for {company}...")

            resume_res = run_resume_tailoring_workflow(
                jd_text=job["jd_text"],
                max_iterations=3,
                quality_threshold=85
            )

            if generate_cover_letters:
                run_cover_letter_workflow(
                    jd_text=job["jd_text"],
                    company_name=company,
                    max_iterations=3,
                    quality_threshold=85
                )

            if resume_res and resume_res.get("audit_report", {}).get("passed_audit"):
                tailored = resume_res["tailored_output"]

                doc_res = generate_tailored_resume_docx(
                    company_name=company,
                    role_title=role,
                    tailored_summary=tailored["tailored_summary"],
                    tailored_bullets=tailored["tailored_experience_bullets"]
                )

                drive_resume_link = doc_res.get('drive_url')
                print(f"✅ Document Generated: {doc_res.get('local_path')}")
                print(f"☁️ Google Drive Link: {drive_resume_link}")

        # ----------------------------------------------------------------------
        # FIRESTORE PERSISTENCE
        # ----------------------------------------------------------------------
        matched_dm = hiring_manager_dms.get(company.lower().strip())

        db.save_application_record(
            company=company,
            title=role,
            location=location,
            match_score=match_score,
            strategy=strategy.priority,
            drive_resume_link=drive_resume_link,
            drive_cover_letter_link=drive_cover_letter_link,
            hiring_manager_dm=matched_dm,
            job_url=job_url
        )

    # --------------------------------------------------------------------------
    # STEP 4: DAILY EXECUTIVE EMAIL DISPATCH
    # --------------------------------------------------------------------------
    print("\n📧 STEP 4: Generating Daily Executive Briefing & Email Dispatch...")
    daily_records = db.get_daily_standup_summary()

    if daily_records:
        dispatched = send_executive_briefing(daily_records)
        if dispatched:
            print("✅ Executive briefing emailed successfully!")
        else:
            print("⚠️ Email dispatch failed or skipped (check credentials).")
    else:
        print("ℹ️ No new application records generated today to email.")

    print("\n" + generate_daily_standup_report())


if __name__ == "__main__":
    run_modular_executive_pipeline(generate_cover_letters=True)