# Personal Supervisor — An Autonomous Job Search Agent

This project runs your job search for you. Every day, it goes out and looks for
relevant openings and hiring-manager posts, reads each job description, checks
how well it actually matches your real experience, decides whether it's worth
applying to, tailors a resume and cover letter for the ones worth pursuing, and
emails you a short daily briefing with everything it found and prepared.

You still make the final call on every application — the system prepares
everything and gets out of your way, it doesn't submit anything on its own.

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