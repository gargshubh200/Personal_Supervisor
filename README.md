# Personal Supervisor — An Autonomous Job Search Agent

This project runs your job search for you. Every day, it goes out and looks for
relevant openings and hiring-manager posts, reads each job description, checks
how well it actually matches your real experience, decides whether it's worth
applying to, tailors a resume and cover letter for the ones worth pursuing, and
emails you a short daily briefing with everything it found and prepared.

You still make the final call on every application — the system prepares
everything and gets out of your way, it doesn't submit anything on its own.

---

## Disclaimer

This is a personal project, published as-is for reference. A few things to
keep in mind before you clone it:

1. **Everything here is tuned for my own job search.** The project plan,
   folder structure, LLM system prompts, MCP tool logic, and `config.yaml`
   (search roles, target companies, locations, dealbreakers/preferences) all
   reflect my specific role targets, experience, and constraints — not a
   generic template. The codebase is small and clearly organized on purpose:
   if you want to point this at a different role, seniority level, or
   geography, feeding the repo to an LLM coding assistant and asking it to
   retarget `config.yaml` (and, if needed, the prompts in `SubAgents/`) is a
   quick, reliable way to adapt it. It was not built to be a plug-and-play
   product for arbitrary job searches out of the box.

2. **Gemini is the only LLM this project has been built and tested against.**
   I built this end-to-end on Google's Agent Development Kit (ADK), and
   Gemini has worked well for every task here — structured JD parsing, match
   scoring, resume/cover-letter generation, and outreach drafting. Nothing
   about the architecture requires Gemini specifically: ADK supports other
   model providers, but swapping one in would need some code-level changes
   (client setup, response-schema handling, occasional prompt tuning) rather
   than a config flip. As with the customization point above, an LLM coding
   assistant can carry out that kind of provider swap quickly — it just isn't
   done here since Gemini already meets my needs.

3. **This automates preparation, not submission.** It drafts resumes, cover
   letters, and outreach messages — it never submits an application or sends
   a message on your behalf. Every generated claim is checked against your
   own `master_profile.json` (see "A note on trust" below), but you are still
   responsible for reviewing anything before it goes out under your name.

4. **You are responsible for how you use third-party data and services.**
   This project scrapes public job boards, LinkedIn posts, and company career
   pages via Apify actors, Serper, and Tavily, and sends data to Google's
   Gemini API. Review each platform's terms of service and robots.txt/rate
   limits for your own use, keep your API keys and `.env` private, and be
   mindful of how much load you put on any single source — the built-in
   toggles, rate-limit delays, and per-source limits exist specifically to
   keep usage reasonable, not to guarantee compliance on your behalf.

5. **No warranty, no guaranteed outcomes.** This is shared for learning and
   reuse, not as a maintained product. It comes with no guarantee of
   continued compatibility with Apify actors, LinkedIn/job-board structure
   changes, or any LLM provider's API — all of these can and do change
   without notice, and you may need to patch scrapers/prompts accordingly.

6. **Some companies are intentionally not covered by a dedicated scraper.**
   `ats_direct_mcp.py` only queries companies whose careers page is powered by
   Greenhouse, Lever, Ashby, or Workable — each of these exposes a free,
   stable, unauthenticated JSON API, which is what makes fast, low-maintenance
   direct fetching possible. A number of well-known remote-first companies
   (see `no_public_ats_companies` in `config.yaml`) run on Workday, BambooHR,
   Teamtailor, SmartRecruiters, or a fully custom in-house portal instead —
   platforms that don't expose an equivalent public API (Workday's URL scheme
   differs per tenant and often needs session cookies; BambooHR/Teamtailor
   render postings client-side). Building and maintaining a bespoke scraper
   per platform for a short, fixed list of companies — several of which post
   roles matching this candidate's target domains only rarely — has a poor
   effort-to-lead ratio and a high breakage rate every time one of those
   portals redesigns. Instead, those companies are covered opportunistically
   through ordinary Google-Dork queries scoped to their own domain (see the
   architecture note at the top of `ats_google_dork_mcp.py`), reusing the same
   Serper/Tavily pipeline already in place rather than adding new fetcher
   code. If you fork this for a company that's since added a supported ATS,
   just move it into `ats_target_companies` in `config.yaml`.

7. **The MCPs here are personal integration scripts, not standalone MCP
   servers meant to be packaged/deployed on their own (e.g. as a Docker
   image others `docker pull` and run).** They're called "MCP" because
   they're written against the Model Context Protocol server interface for
   consistency and easy tool-calling from the agents in this repo — but each
   one is a thin, purpose-built wrapper around someone else's existing
   service: Apify actors (for LinkedIn/Indeed/Glassdoor/Wellfound/AmbitionBox
   scraping), Serper/Tavily (for search & extraction), and the ATS vendors'
   own public job-board APIs (Greenhouse/Lever/Ashby/Workable). There would be
   nothing authentic about re-packaging those into a standalone "product" —
   the value here is the custom orchestration, filtering, and decision logic
   built *on top of* those services for this specific job search, not the
   underlying scraping/search capability itself, which isn't ours to
   re-distribute as an independent offering. None of this is intended to
   duplicate, circumvent, or resell any third-party's service — it's a
   personal automation that happens to call several existing platforms via
   their intended, documented APIs.

---

## Why this exists

Job hunting is mostly repetitive busywork: searching the same boards every day,
reading dozens of descriptions that don't fit, and rewriting the same resume
bullets for each company. This project automates that repetitive part and
leaves you with a short, curated list of leads worth spending your time on —
each one already accompanied by a tailored resume and cover letter.

---

## How it thinks about you

Your real work history lives in one file: `master_profile.json`. It's a
structured version of your resume — companies, roles, employer-scoped
projects, a standalone "Projects" section (personal/side projects not tied to
any job), verified metrics (the kind of thing you'd normally have to remember
to mention), and skills. Every downstream step — matching, resume tailoring,
cover letters, outreach messages — pulls facts from this file instead of
inventing them, so nothing gets exaggerated or made up along the way.

You don't have to maintain this file by hand. Drop an updated resume
(`.docx` or `.pdf`) into `CandidateResumeDoc/`, and the system will read it and
regenerate `master_profile.json` automatically the next time it runs.

---

## What happens on a single run

Each run walks through the same sequence of steps, in order:

1. **Refresh your profile.** If there's a resume sitting in `CandidateResumeDoc/`,
   it's parsed and `master_profile.json` is regenerated from it. If there's no
   resume there, the existing profile is used as-is.

2. **Look for hiring-manager posts on LinkedIn.** Instead of only reading job
   board listings, the system also searches LinkedIn for posts where someone is
   actively hiring — those posts tend to be fresher and less crowded than a
   public job listing. Every non-genuine author (recruiters, staffing
   agencies, job-board pages) is filtered out, and each remaining post is
   checked for whether it genuinely mentions the role *and* a real hiring-intent
   phrase (accepting reasonable phrasing variants, e.g. "we are hiring" as well
   as "we're hiring") — not just something the search happened to surface — and
   a short outreach message is drafted for the ones that pass.

3. **Collect job listings.** Several sources are checked in parallel — direct
   company career pages, targeted search-engine queries against known applicant
   tracking systems, and LinkedIn job search. Anything from a company you've
   excluded, in a location you don't want, or already seen in a previous run is
   dropped before it reaches the next step.

4. **Evaluate every remaining job, one at a time:**
   - Read and structure the job description (seniority, must-have skills, etc.).
   - Score how well it matches your actual, verified experience.
   - Decide: apply, or skip — and if applying, at what priority.
   - If it's worth applying to, tailor your resume's summary and bullet points
     to that specific job (still only using facts already in your profile),
     generate a matching cover letter, save both as Google Docs, and get you a
     shareable link.

5. **Send you a daily briefing.** One email summarizing every new lead, hiring
   manager conversation, match score, and generated document link from that
   run — plus a plain console summary if you're running it locally.

Every job and hiring lead is saved to a small database so nothing gets
processed twice across runs.

---

## The moving pieces

```
CandidateResumeDoc/        Drop your latest resume here — auto-detected each run
master_profile.json        Your structured, verified work history (auto-generated)
config.yaml                 All tunable setup data — toggles, search terms, locations,
                             target companies, career strategy constraints (edit this,
                             not the Python files, to change behavior)
config_loader.py            Loads & caches config.yaml for every MCP/SubAgent
supervisor_agent.py         The orchestrator — runs the steps above in order

JobSearchMCP/               Where job leads come from
  linkedin_hiring_posts_mcp.py    LinkedIn hiring-manager post search
  linkedin_job_scraper_mcp.py     LinkedIn job listing search
  ats_direct_mcp.py               Direct search of known companies' career pages (Greenhouse/Lever/Ashby/Workable)
  ats_google_dork_mcp.py          Search-engine based discovery of ATS job pages
  indeed_scraper_mcp.py           Indeed job search
  wellfound_scraper_mcp.py        Wellfound (startup jobs) search
  glassdoor_scraper_mcp.py        Glassdoor job search
  ambitionbox_scraper_mcp.py      AmbitionBox (India-focused) job search

SubAgents/                  Where the thinking/writing happens
  master_profile_agent.py         Resume → master_profile.json
  jd_analysis_agent.py            Reads and structures a job description
  matching_agent.py               Scores you against a job's requirements
  application_strategy_agent.py   Applies your personal apply/skip rules
  resume_tailoring_agent.py       Tailors resume content per job
  cover_letter_agent.py           Writes a cover letter per job
  hiring_manager_dm_agent.py      Drafts outreach messages to hiring managers

OtherMCP/                    Supporting utilities (deterministic tools, no LLM calls)
  ground_truth_mcp.py              Guardrail — flags unverifiable claims
  dispatch_mcp.py                  Builds resume/cover-letter Google Docs
  notification_mcp.py             Sends the daily briefing email
  personal_ops_mcp.py              Logs applications & builds the daily activity summary

db_manager.py               Tracks processed jobs/leads so nothing repeats
```

### The flow, visually

```
                     ┌─────────────────────────────┐
                     │  CandidateResumeDoc/*.docx   │
                     └──────────────┬────────────────┘
                                    │ Step 0
                                    ▼
                     ┌─────────────────────────────┐
                     │      master_profile.json      │◄──────────────┐
                     └──────────────┬────────────────┘                │
                                    │                                  │ facts only,
        ┌───────────────────────────┼───────────────────────────┐      │ never invented
        │ Step 1                    │ Step 2                    │      │
        ▼                          ▼                            │      │
┌───────────────────┐   ┌────────────────────────┐              │      │
│ LinkedIn hiring-   │   │ Job boards & ATS search │              │      │
│ manager posts      │   │ (LinkedIn, Indeed, ATS  │              │      │
│                    │   │  pages, Wellfound, etc.)│              │      │
└─────────┬──────────┘   └────────────┬────────────┘              │      │
          │                            │                          │      │
          ▼                            ▼                          │      │
   draft outreach DM         dedupe + filter by                   │      │
   (skips if invalid)        company / location / seen-before      │      │
          │                            │                          │      │
          │                            ▼   Step 3, per job         │      │
          │              ┌───────────────────────────┐             │      │
          │              │ Read job description        │◄───────────┘      │
          │              │ Score match vs. your profile │──────────────────┘
          │              │ Decide: apply or skip         │
          │              │ If applying → tailor resume    │
          │              │   + cover letter (Google Docs)  │
          │              └───────────────┬─────────────────┘
          │                              │
          └──────────────┬───────────────┘
                          ▼ Step 4
              ┌─────────────────────────┐
              │  Daily briefing email    │
              │  + saved to database     │
              └─────────────────────────┘
```

---

## Running it yourself

### 1. Requirements

- Python 3.11+
- A Google Cloud project with the **Vertex AI** and **Firestore** APIs enabled
  (used for the "thinking" steps and for tracking processed jobs/leads)
- An [Apify](https://apify.com) account (used for the LinkedIn/job-board search actors)
- A Gmail account with an
  [App Password](https://support.google.com/accounts/answer/185833) (used to
  send you the daily briefing)
- A Google Drive folder (where tailored resumes/cover letters get saved) and
  OAuth credentials for the Google Drive/Docs API

### 2. Install

```bash
pip install -r requirements.txt
```

### 3. Configure your credentials

Copy your own values into a `.env` file in the project root:

```env
# Google Drive & Email
GCS_RESUME_BUCKET=career-os-resumes-bucket
GOOGLE_DRIVE_FOLDER_ID=your_drive_folder_id
SENDER_EMAIL=you@gmail.com
SENDER_APP_PASSWORD=your_gmail_app_password
RECIPIENT_EMAIL=you@gmail.com

# Observability (optional — leave blank to disable tracing)
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_BASE_URL=

# API keys
GEMINI_API_KEY=            # not required if using Vertex AI (recommended)
APIFY_API_TOKEN=your_apify_token
SERPER_API_KEY=your_serper_key
TAVILY_API_KEY=your_tavily_key
```

You'll also need:
- `OtherMCP/credentials.json` — OAuth client credentials for Google
  Drive/Docs (downloaded from Google Cloud Console). The first run will open a
  browser to authorize and save a `token.json` next to it.
- Google Cloud authentication for Vertex AI and Firestore — either run
  `gcloud auth application-default login` locally, or set
  `GOOGLE_APPLICATION_CREDENTIALS` to point at a service account key file.
  Update the hardcoded `project="career-os-project"` references in the
  `SubAgents/*.py` files and `db_manager.py` to your own GCP project ID.

### 4. Add your resume

Put your resume (`.docx` or `.pdf`) in `CandidateResumeDoc/`. It'll be picked
up and turned into `master_profile.json` automatically on the next run.

### 5. Pick which sources to search

All tunable setup data — which sources run (`mcp_toggles`), your target roles
and locations (`search.master_search_queries` / `search.master_locations`),
your dealbreakers and preferences (`career_strategy_constraints`), and the
list of companies `ats_direct_mcp.py` checks directly (`ats_target_companies`)
— lives in **`config.yaml`** at the repo root. Open it and edit the plain
YAML lists/dicts; no Python code changes needed. Regexes and LLM prompts stay
in the source files since they're engineering logic, not user-tunable setup.

### 6. Run it

```bash
python supervisor_agent.py
```

### Running in Docker instead

```bash
docker build -t personal-supervisor .
docker run --env-file .env personal-supervisor
```

---

### 7. Deploying to Google Cloud Run

```
# Git Bash / WSL / Linux / macOS terminal
chmod +x deploy_gcp.sh
./deploy_gcp.sh
```

```
# IDE Terminal
# Set the  terminal to Git Bash
./deploy_gcp.sh
```

```
# Run
gcloud run jobs execute career-os-supervisor-job --region asia-south1
```

---

### 8. Schedule the Gcloud Run job

```
# Schedule
PROJECT_ID="" # your GCP project ID
REGION="asia-south1"
JOB_NAME="career-os-supervisor-job"
SCHEDULER_NAME="career-os-supervisor-daily-trigger"
TIME_ZONE="" # e.g., America/New_York, UTC, Asia/Kolkata

PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud scheduler jobs create http "${SCHEDULER_NAME}" \
    --location="${REGION}" \
    --schedule="0 8 * * *" \ # this is set for 8 AM daily; adjust as needed
    --time-zone="${TIME_ZONE}" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
    --http-method="POST" \
    --oauth-service-account-email="${SERVICE_ACCOUNT}"
```

---

### 9. Updating your resume in the gcs bucket

A gcs bucket `career-os-resumes-bucket` is used to upload the candidate resume & for persisting the output resumes. \
User can upload the updated resume (doc/pdf, any file name) in the base_resume folder within the bucket 

---

## A note on trust

Every generated resume bullet, cover letter claim, and outreach message is
checked against `master_profile.json` before being used. If a step tries to
introduce a number or achievement that isn't backed by that file, it's
rejected rather than sent out. The goal is that everything the system
produces on your behalf is something you'd be comfortable standing behind in
an interview.

## A note on how Gemini is called

Every agent in `SubAgents/` uses a single-pass `client.models.generate_content()`
call — either asking for a structured (Pydantic) response, like a job analysis
or a match score, or a plain piece of text, like a cover letter or an outreach
message. None of them run a multi-turn conversation where Gemini decides on
its own to call other functions in a loop.

That's intentional: this project's tasks are naturally one-shot (read this,
score it, write that), and Google's own guidance is that automatic function
calling — where the model autonomously chooses and invokes tools — should
only be done through a chat session (`client.chats.create()` +
`chat.send_message()`), not through `generate_content()` directly. If a future
addition to this project needs Gemini to decide, mid-task, to call one or
more Python functions and react to their results, that step should be built
as a chat session rather than added onto an existing `generate_content()` call.

## A note on generating your own agent workflow graph (in case you make architectural changes)

Using Graphviz (MERMAID output is already present in the README above):

If your pipeline is built with custom Python orchestration (like supervisor_agent.py), 
the graphviz library generates clean, publication-ready flowcharts rendered directly as PNGs.

Prerequisites
```bash
pip install graphviz
```
Requires the system-level Graphviz library installed on your OS, e.g., 
`winget install graphviz` on Windows or 
`brew install graphviz` on macOS

Generating the workflow graph:

```bash
python generate_workflow_graph.py
```
After running the above command, a PNG file named `career_os_workflow_graph.png` will be generated 
in the project directory, visualizing the entire agent workflow.