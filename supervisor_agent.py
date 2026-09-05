import os, time
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
        print(f"--------------------------------------------------\n{dm}\n--------------------------------------------------")
        time.sleep(3)  # Throttle between DM generation calls

    # --------------------------------------------------------------------------
    # STEP 2: MULTI-PLATFORM JOB BOARD & ATS INGESTION
    # --------------------------------------------------------------------------
    print("\n🌐 STEP 2: Ingesting Job Listings across Platform MCPs & ATS Engines...")
    raw_jobs = []

    # 1. Direct ATS Public APIs (Target Company List: Stripe, Palantir, Scale AI, Ramp)
    raw_jobs.extend(fetch_ats_direct_jobs(limit=5))

    # 2. ATS Google Dorking (Serper Discovery + Tavily Full Markdown Extract)
    raw_jobs.extend(search_ats_via_google_dork(limit=5))

    # 3. LinkedIn: All 9 roles (Batched internally into 3-role Boolean chunks)
    raw_jobs.extend(
        fetch_linkedin_jobs(
            search_queries=MASTER_SEARCH_QUERIES,
            locations=MASTER_LOCATIONS,
            limit_per_query=2
        )
    )

    # 4. Indeed: Top 3 roles
    raw_jobs.extend(
        fetch_indeed_jobs(
            search_queries=active_roles_batch,
            locations=["India", "Remote"],
            limit=2
        )
    )

    # 5. Wellfound: Focused startup role
    raw_jobs.extend(
        fetch_wellfound_jobs(
            search_queries=[active_roles_batch[1]],
            locations=MASTER_LOCATIONS,
            limit=1
        )
    )

    # 6. Glassdoor: Top 3 roles
    raw_jobs.extend(
        fetch_glassdoor_jobs(
            search_queries=active_roles_batch,
            locations=MASTER_LOCATIONS,
            limit=1
        )
    )

    # 7. AmbitionBox: Focused role for India tech hubs
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
    # STEP 3: MULTI-AGENT PROCESSING & GATED WORKFLOW EXECUTION
    # --------------------------------------------------------------------------
    for idx, job in enumerate(all_jobs):
        print(f"\n⚙️ PROCESSING [{idx + 1}/{len(all_jobs)}]: [{job['platform']}] {job['company']} - {job['role']}")

        # Throttle requests to respect Gemini RPM free rate limits
        if idx > 0:
            print("⏳ Throttling for 12 seconds to respect Gemini free rate limits...")
            time.sleep(12)

        # 1. Parse Job Description Features
        print("🧠 [Agent 1/5] Extracting JD Keywords & Stack Requirements...")
        jd_analysis = analyze_job_description(job["jd_text"])

        # 2. Run Deterministic Grounded Match Evaluation
        print("📊 [Agent 2/5] Evaluating Candidate Requirement Match...")
        matching_report = evaluate_candidate_match(jd_analysis=jd_analysis, threshold=60.0)
        print(f"   ↳ {matching_report.summary}")

        # 3. Formulate Application Strategy & Priority Ranking
        print("🎯 [Agent 3/5] Determining Application Strategy & Priority Tier...")
        strategy = determine_application_strategy(
            company_name=job["company"],
            jd_analysis=jd_analysis,
            matching_report=matching_report
        )
        print(f"   ↳ Strategy: {strategy.decision} | Priority: {strategy.priority} | Tailor Resume: {strategy.should_tailor_resume}")

        # ----------------------------------------------------------------------
        # CONDITIONAL GATEWAY:
        # Downstream tailoring and document generation run ONLY if strategy approves
        # ----------------------------------------------------------------------
        if strategy.decision == "APPLY" and strategy.should_tailor_resume == "YES":
            print(f"🚀 Moving forward with resume tailoring & document dispatch for {job['company']}...")

            # 4. Run Multi-Pass Resume Tailoring Workflow
            print("⚡ [Agent 4/5] Running Resume Tailoring, Safety Audit & Review Loop...")
            resume_res = run_resume_tailoring_workflow(
                jd_text=job["jd_text"],
                max_iterations=3,
                quality_threshold=85
            )

            # 5. Run Multi-Pass Cover Letter Workflow (Optional)
            cover_letter_res = None
            if generate_cover_letters:
                print("✍️ [Agent 5/5] Running Cover Letter Writer, Safety Audit & Review Loop...")
                cover_letter_res = run_cover_letter_workflow(
                    jd_text=job["jd_text"],
                    company_name=job["company"],
                    max_iterations=3,
                    quality_threshold=85
                )

            # 6. ATS Document Generation & Cloud Dispatch
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
                    matched_keywords=jd_analysis.ats_keywords
                )

                print(f"✅ Generated ATS Resume Local: {doc_res.get('local_path')}")
                print(f"☁️ Google Drive Link: {doc_res.get('drive_url')}")

                if cover_letter_res:
                    cl_score = cover_letter_res.get('review_report', {}).get('overall_score')
                    print(f"✍️ Tailored Cover Letter Score: {cl_score}/100")
        else:
            print(f"⏭️ SKIPPING downstream tailoring for {job['company']}. Reason: Strategy designated as {strategy.decision}.")

    # --------------------------------------------------------------------------
    # STEP 4: DAILY STANDUP REPORT
    # --------------------------------------------------------------------------
    print("\n" + generate_daily_standup_report())


if __name__ == "__main__":
    run_modular_executive_pipeline(generate_cover_letters=True)