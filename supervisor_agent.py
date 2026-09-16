import os
from dotenv import load_dotenv
from langfuse import observe

from config_loader import (
    get_mcp_toggles,
    get_master_search_queries,
    get_master_locations,
    get_career_strategy_constraints,
)

from JobSearchMCP.ats_direct_mcp import fetch_ats_direct_jobs
from JobSearchMCP.ats_google_dork_mcp import search_ats_via_google_dork
from JobSearchMCP.linkedin_job_scraper_mcp import fetch_linkedin_jobs
from JobSearchMCP.linkedin_hiring_posts_mcp import search_hiring_manager_posts
from JobSearchMCP.indeed_scraper_mcp import fetch_indeed_jobs
from JobSearchMCP.wellfound_scraper_mcp import fetch_wellfound_jobs
from JobSearchMCP.glassdoor_scraper_mcp import fetch_glassdoor_jobs
from JobSearchMCP.ambitionbox_scraper_mcp import fetch_ambitionbox_jobs

from SubAgents.hiring_manager_dm_agent import generate_hiring_manager_dm
from SubAgents.jd_analysis_agent import analyze_job_description
from SubAgents.matching_agent import evaluate_candidate_match
from SubAgents.application_strategy_agent import determine_application_strategy
from SubAgents.resume_tailoring_agent import run_resume_tailoring_workflow
from SubAgents.cover_letter_agent import run_cover_letter_workflow

from OtherMCP.personal_ops_mcp import generate_daily_standup_report
from OtherMCP.ground_truth_mcp import _load_profile
from OtherMCP.dispatch_mcp import generate_tailored_resume_docx, generate_cover_letter_docx

from SubAgents.master_profile_agent import generate_master_profile, find_latest_resume_in_candidate_folder

from db_manager import DatabaseManager
from OtherMCP.notification_mcp import send_executive_briefing

load_dotenv()

# All toggles, search terms, locations, and career strategy constraints now
# live in config.yaml (repo root) — edit that file to tune behavior without
# touching this code. See config_loader.py for the loading/caching logic.
MCP_TOGGLES = get_mcp_toggles()
MASTER_SEARCH_QUERIES = get_master_search_queries()
MASTER_LOCATIONS = get_master_locations()
CAREER_STRATEGY_CONSTRAINTS = get_career_strategy_constraints()


def is_location_eligible(job_location: str, allowed_locations: list) -> bool:
    if not job_location or job_location.upper() == "N/A":
        return True
    loc_lower = job_location.lower()
    return any(target.lower() in loc_lower for target in allowed_locations) or "remote" in loc_lower


@observe(name="Supervisor Agent: Modular MCP Multi-Agent Run")
def run_modular_executive_pipeline(generate_cover_letters: bool = True):
    print("==================================================")
    print("🤖 SUPERVISOR AGENT: MODULAR MCP ORCHESTRATION")
    print("==================================================")

    db = DatabaseManager()

    # --------------------------------------------------------------------------
    # STEP 0: REFRESH MASTER PROFILE FROM LATEST RESUME (if one is present)
    # --------------------------------------------------------------------------
    print("\n📄 STEP 0: Checking CandidateResumeDoc/ for an updated resume...")
    try:
        resume_path = find_latest_resume_in_candidate_folder()
        if resume_path:
            print(f"🧠 Found resume '{resume_path.name}' — regenerating master_profile.json...")
            generate_master_profile(str(resume_path))
            print("✅ STEP 0 COMPLETE: master_profile.json refreshed from latest resume.")
        else:
            print("ℹ️  No resume found in CandidateResumeDoc/. Using existing master_profile.json as-is.")
    except Exception as e:
        print(f"⚠️ Warning during master profile regeneration: {str(e)}. Proceeding with existing master_profile.json.")

    # --------------------------------------------------------------------------
    # STEP 1: INDEPENDENT LINKEDIN HIRING MANAGER DISCOVERY
    # --------------------------------------------------------------------------
    if MCP_TOGGLES.get("linkedin_hiring_posts"):
        print("\n🎯 STEP 1: Discovering & Processing Hiring Managers on LinkedIn...")
        try:
            leads = search_hiring_manager_posts(
                search_queries=None,  # Use module's optimized natural-language DEFAULT_HIRING_POST_ROLES
                locations=MASTER_LOCATIONS,
                limit_per_pattern=2  # 4 roles x 4 patterns = 16 sequential actor calls; kept small to avoid overload
            )

            profile = _load_profile()
            processed_leads_count = 0

            for idx, lead in enumerate(leads):
                manager_name = lead.get('manager_name', 'Hiring Manager')
                company_name = lead.get('company') or 'Tech Company'
                post_url = lead.get('post_url', '')

                if db.is_hiring_lead_processed(post_url=post_url, manager_name=manager_name, company=company_name):
                    print(f"⏭️ Skipping previously processed lead: {manager_name} ({company_name})")
                    continue

                print(f"📩 Drafting DM [{idx + 1}/{len(leads)}]: {manager_name} ({company_name})")
                dm = generate_hiring_manager_dm(lead, profile)

                if not dm.startswith("[OUTREACH SKIPPED"):
                    db.save_hiring_lead_record(
                        manager_name=manager_name,
                        manager_title=lead.get('manager_title', ''),
                        company=company_name,
                        post_url=post_url,
                        post_text=lead.get('post_text', ''),
                        drafted_dm=dm
                    )
                    processed_leads_count += 1

            print(f"✅ STEP 1 COMPLETE: {processed_leads_count} new hiring manager DMs saved to Firestore.")
        except Exception as e:
            print(f"⚠️ Warning during hiring manager discovery: {str(e)}")

    # --------------------------------------------------------------------------
    # STEP 2: MULTI-PLATFORM JOB BOARD & ATS INGESTION
    # --------------------------------------------------------------------------
    print("\n🌐 STEP 2: Ingesting Job Listings across Enabled Platform MCPs...")
    raw_jobs = []

    if MCP_TOGGLES.get("ats_direct"):
        raw_jobs.extend(fetch_ats_direct_jobs(per_company_limit=3))
    if MCP_TOGGLES.get("ats_google_dork"):
        raw_jobs.extend(search_ats_via_google_dork(limit=20))
    if MCP_TOGGLES.get("linkedin_jobs"):
        raw_jobs.extend(fetch_linkedin_jobs(search_queries=MASTER_SEARCH_QUERIES, locations=MASTER_LOCATIONS, limit_per_query=10))
    if MCP_TOGGLES.get("indeed"):
        raw_jobs.extend(fetch_indeed_jobs(search_queries=MASTER_SEARCH_QUERIES, locations=MASTER_LOCATIONS, limit=10))
    if MCP_TOGGLES.get("wellfound"):
        raw_jobs.extend(fetch_wellfound_jobs(search_queries=MASTER_SEARCH_QUERIES, locations=MASTER_LOCATIONS, limit=10))
    if MCP_TOGGLES.get("glassdoor"):
        raw_jobs.extend(fetch_glassdoor_jobs(search_queries=MASTER_SEARCH_QUERIES, locations=MASTER_LOCATIONS, limit=10))
    if MCP_TOGGLES.get("ambitionbox"):
        raw_jobs.extend(fetch_ambitionbox_jobs(search_queries=MASTER_SEARCH_QUERIES, locations=MASTER_LOCATIONS, limit=5))

    seen_signatures = set()
    all_jobs = []

    for j in raw_jobs:
        company = j.get('company', '').strip()
        role = j.get('role', '').strip()
        location_raw = j.get('location', 'India')
        # Defensive normalization: some sources may (incorrectly) return a list of
        # location names instead of a plain string. Guard against this regardless
        # of source, so a future MCP regression doesn't crash the whole pipeline.
        if isinstance(location_raw, list):
            location = ", ".join(str(x) for x in location_raw).strip() or "India"
        else:
            location = str(location_raw).strip() or "India"
        sig = f"{company.lower()}:{role.lower()}"

        if any(ex_comp in company.lower() for ex_comp in CAREER_STRATEGY_CONSTRAINTS["excluded_companies"]):
            continue
        if not is_location_eligible(location, MASTER_LOCATIONS):
            continue
        if sig in seen_signatures or db.is_job_processed(company, role):
            continue

        seen_signatures.add(sig)
        all_jobs.append(j)

    print(f"✅ Deduplication Summary: {len(all_jobs)} new DISCOVERED job leads retained.")

    # --------------------------------------------------------------------------
    # STEP 3: MULTI-AGENT PROCESSING & STATE TRANSITION PIPELINE
    # --------------------------------------------------------------------------
    for idx, job in enumerate(all_jobs):
        company = job['company']
        role = job['role']
        location = job.get('location', 'India')
        job_url = job.get('url', '') or job.get('job_url', '')

        print(f"\n⚙️ PROCESSING [{idx + 1}/{len(all_jobs)}]: {company} - {role}")
        current_lifecycle_status = "DISCOVERED"

        # 1. Parse Job Description
        jd_analysis = analyze_job_description(job["jd_text"])

        # 2. Run Grounded Match Evaluation
        matching_report = evaluate_candidate_match(jd_analysis=jd_analysis, threshold=60.0)
        overall_score = int(matching_report.deterministic_score)
        core_score = int(matching_report.core_capability_score)
        current_lifecycle_status = "ANALYZED"

        # 3. Formulate Application Strategy (EV Funnel)
        strategy = determine_application_strategy(
            company_name=company,
            role_title=role,
            jd_analysis=jd_analysis,
            matching_report=matching_report,
            career_constraints=CAREER_STRATEGY_CONSTRAINTS
        )

        if strategy.decision == "SKIP":
            current_lifecycle_status = "SKIPPED"
        else:
            current_lifecycle_status = "SHORTLISTED"

        drive_resume_link = None
        drive_cover_letter_link = None

        # 4. Tailoring & Document Dispatch Gateway
        if strategy.decision == "APPLY" and strategy.should_tailor_resume == "YES":
            resume_res = run_resume_tailoring_workflow(
                jd_text=job["jd_text"],
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
                current_lifecycle_status = "TAILORED"

            if generate_cover_letters:
                cl_res = run_cover_letter_workflow(
                    jd_text=job["jd_text"],
                    company_name=company,
                    max_iterations=3,
                    quality_threshold=85
                )

                if cl_res and cl_res.get("audit_report", {}).get("passed_audit"):
                    cl_doc_res = generate_cover_letter_docx(
                        company_name=company,
                        role_title=role,
                        cover_letter_data=cl_res["cover_letter"]
                    )
                    drive_cover_letter_link = cl_doc_res.get('drive_url')

            if drive_resume_link:
                current_lifecycle_status = "READY_TO_APPLY"

        # 5. Persist Full Application Record with Rich Lifecycle State
        db.save_application_record(
            company=company,
            title=role,
            location=location,
            match_score=overall_score,
            core_capability_score=core_score,
            strategy=strategy.priority,
            decision=strategy.decision,
            status=current_lifecycle_status,
            dealbreaker_triggered=strategy.dealbreaker_triggered,
            dealbreaker_reason=strategy.dealbreaker_reason,
            drive_resume_link=drive_resume_link,
            drive_cover_letter_link=drive_cover_letter_link,
            job_url=job_url
        )

    # --------------------------------------------------------------------------
    # STEP 4: DAILY EXECUTIVE EMAIL DISPATCH
    # --------------------------------------------------------------------------
    print("\n📧 STEP 4: Generating Daily Executive Briefing & Email Dispatch...")
    daily_summary = db.get_daily_standup_summary()

    if daily_summary["job_applications"] or daily_summary["hiring_leads"]:
        send_executive_briefing(daily_summary)

    print("\n" + generate_daily_standup_report(applications=daily_summary["job_applications"]))


if __name__ == "__main__":
    run_modular_executive_pipeline(generate_cover_letters=True)