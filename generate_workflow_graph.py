"""
Renders an up-to-date architecture/workflow diagram of the Career OS pipeline
(supervisor_agent.py's run_modular_executive_pipeline: Step 0 -> Step 4).

Run: python generate_workflow_graph.py
Output: career_os_workflow_graph.png (and the intermediate .gv source file)
"""
import graphviz

# ------------------------------------------------------------------
# Shared visual language
# ------------------------------------------------------------------
COLOR_PROFILE = "#E1F5FE"      # light blue   — profile / ground truth
COLOR_DISCOVERY = "#FFF3E0"    # light amber  — lead discovery (job/post sources)
COLOR_FILTER = "#F3E5F5"       # light purple — dedup / gating logic
COLOR_AGENT = "#E8F5E9"        # light green  — LLM reasoning agents
COLOR_DECISION = "#FFF9C4"     # light yellow — decision points
COLOR_TOOL = "#FCE4EC"         # light pink   — deterministic tools/guardrails
COLOR_STORE = "#ECEFF1"        # light grey   — persistence
COLOR_OUTPUT = "#D1C4E9"       # light violet — outbound notification

FONT = "Helvetica"


def draw_career_os_workflow():
    dot = graphviz.Digraph(comment="Career OS Agentic Workflow", format="png")
    dot.attr(
        rankdir="TB",
        splines="polyline",
        nodesep="0.55",
        ranksep="0.75",
        bgcolor="white",
        fontname=FONT,
        fontsize="20",
        label="Career OS — Autonomous Job Search Pipeline\n(supervisor_agent.py: Step 0 \u2192 Step 4)",
        labelloc="t",
    )
    dot.attr(
        "node",
        fontname=FONT,
        fontsize="11",
        shape="box",
        style="rounded,filled",
        color="#455A64",
        fillcolor="white",
        margin="0.18,0.12",
    )
    dot.attr("edge", fontname=FONT, fontsize="9.5", color="#607D8B", arrowsize="0.8")

    # ------------------------------------------------------------------
    # STEP 0 — Profile refresh
    # ------------------------------------------------------------------
    with dot.subgraph(name="cluster_step0") as c:
        c.attr(label="STEP 0 \u2014 Refresh Ground Truth", style="rounded", color="#0288D1", fontname=FONT, fontsize="12")
        c.node("resume_doc", "CandidateResumeDoc/\n(latest .docx / .pdf)", fillcolor=COLOR_PROFILE)
        c.node("master_profile_agent", "master_profile_agent\n(LLM: resume \u2192 structured profile)", fillcolor=COLOR_AGENT)
        c.node("master_profile_json", "master_profile.json\n(verified work history)", shape="cylinder", fillcolor=COLOR_STORE)

    # ------------------------------------------------------------------
    # STEP 1 — Hiring-manager post discovery
    # ------------------------------------------------------------------
    with dot.subgraph(name="cluster_step1") as c:
        c.attr(label="STEP 1 \u2014 LinkedIn Hiring-Manager Discovery", style="rounded", color="#EF6C00", fontname=FONT, fontsize="12")
        c.node("hm_mcp", "linkedin_hiring_posts_mcp\n(roles \u00d7 patterns, rate-limited)", fillcolor=COLOR_DISCOVERY)
        c.node("hm_content_gate", "Content-confirmation gate\n(role + pattern + YOE/recruiter/\nnon-eng filters)", fillcolor=COLOR_TOOL)
        c.node("hm_lead_validate", "_validate_manager_lead\n(reject non-human / job-board leads)", fillcolor=COLOR_TOOL)
        c.node("hm_dm_agent", "hiring_manager_dm_agent\n(LLM: draft outreach DM)", fillcolor=COLOR_AGENT)

    # ------------------------------------------------------------------
    # STEP 2 — Multi-source job ingestion
    # ------------------------------------------------------------------
    with dot.subgraph(name="cluster_step2") as c:
        c.attr(label="STEP 2 \u2014 Job Ingestion (toggle-gated via MCP_TOGGLES)", style="rounded", color="#6A1B9A", fontname=FONT, fontsize="12")
        c.node("ats_direct", "ats_direct_mcp", fillcolor=COLOR_DISCOVERY)
        c.node("ats_dork", "ats_google_dork_mcp", fillcolor=COLOR_DISCOVERY)
        c.node("li_jobs", "linkedin_job_scraper_mcp", fillcolor=COLOR_DISCOVERY)
        c.node("indeed", "indeed_scraper_mcp", fillcolor=COLOR_DISCOVERY)
        c.node("wellfound", "wellfound_scraper_mcp", fillcolor=COLOR_DISCOVERY)
        c.node("glassdoor", "glassdoor_scraper_mcp", fillcolor=COLOR_DISCOVERY)
        c.node("ambitionbox", "ambitionbox_scraper_mcp", fillcolor=COLOR_DISCOVERY)
        c.node(
            "dedup_filter",
            "Dedupe & Filter\n(excluded companies \u00b7 location eligibility\n\u00b7 already-processed in Firestore)",
            fillcolor=COLOR_FILTER,
        )
        # Keep the 7 source nodes visually grouped on one rank
        with c.subgraph() as same_rank:
            same_rank.attr(rank="same")
            for n in ["ats_direct", "ats_dork", "li_jobs", "indeed", "wellfound", "glassdoor", "ambitionbox"]:
                same_rank.node(n)

    # ------------------------------------------------------------------
    # STEP 3 — Per-job agent pipeline
    # ------------------------------------------------------------------
    with dot.subgraph(name="cluster_step3") as c:
        c.attr(label="STEP 3 \u2014 Per-Job Evaluation & Tailoring (runs once per discovered job)", style="rounded", color="#2E7D32", fontname=FONT, fontsize="12")
        c.node("jd_agent", "jd_analysis_agent\n(LLM: structure the JD)", fillcolor=COLOR_AGENT)
        c.node("matching_agent", "matching_agent\n(LLM classify + deterministic\nweighted scoring)", fillcolor=COLOR_AGENT)
        c.node("strategy_agent", "application_strategy_agent\n(hard pre-gate + LLM Expected-Value funnel)",
               shape="diamond", fillcolor=COLOR_DECISION)
        c.node("resume_workflow", "resume_tailoring_agent\n(write \u2192 ground-truth audit \u2192 review loop)", fillcolor=COLOR_AGENT)
        c.node("cover_letter_workflow", "cover_letter_agent\n(write \u2192 ground-truth + opening-hook\naudit \u2192 review loop)", fillcolor=COLOR_AGENT)
        c.node("ground_truth", "ground_truth_mcp.verify_claim\n(guardrail: reject unverified numbers)", fillcolor=COLOR_TOOL)
        c.node("dispatch", "dispatch_mcp\n(render .docx \u2192 upload to Google Drive)", fillcolor=COLOR_TOOL)

    # ------------------------------------------------------------------
    # Persistence & notification
    # ------------------------------------------------------------------
    dot.node("firestore", "Firestore\n(job_applications + hiring_manager_leads)", shape="cylinder", fillcolor=COLOR_STORE)

    with dot.subgraph(name="cluster_step4") as c:
        c.attr(label="STEP 4 \u2014 Daily Executive Briefing", style="rounded", color="#4527A0", fontname=FONT, fontsize="12")
        c.node("standup_summary", "db.get_daily_standup_summary\n(today's applications + leads)", fillcolor=COLOR_STORE)
        c.node("email_notifier", "notification_mcp\n(SMTP email briefing)", fillcolor=COLOR_OUTPUT)
        c.node("console_standup", "personal_ops_mcp\n(console standup digest)", fillcolor=COLOR_OUTPUT)

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------
    dot.edge("resume_doc", "master_profile_agent")
    dot.edge("master_profile_agent", "master_profile_json", label=" regenerates")
    # Ground truth feeds every downstream agent
    dot.edge("master_profile_json", "hm_dm_agent", style="dashed", color="#90A4AE")
    dot.edge("master_profile_json", "matching_agent", style="dashed", color="#90A4AE")
    dot.edge("master_profile_json", "resume_workflow", style="dashed", color="#90A4AE")
    dot.edge("master_profile_json", "cover_letter_workflow", style="dashed", color="#90A4AE")

    dot.edge("hm_mcp", "hm_content_gate", label=" raw posts")
    dot.edge("hm_content_gate", "hm_lead_validate", label=" confirmed leads")
    dot.edge("hm_lead_validate", "hm_dm_agent", label=" verified human lead")
    dot.edge("hm_dm_agent", "firestore", label=" drafted DM")

    for src in ["ats_direct", "ats_dork", "li_jobs", "indeed", "wellfound", "glassdoor", "ambitionbox"]:
        dot.edge(src, "dedup_filter")
    dot.edge("dedup_filter", "jd_agent", label=" new job leads")

    dot.edge("jd_agent", "matching_agent", label=" structured JD")
    dot.edge("matching_agent", "strategy_agent", label=" match report")
    dot.edge("strategy_agent", "resume_workflow", label=" APPLY", color="#2E7D32", fontcolor="#2E7D32")
    dot.edge("strategy_agent", "cover_letter_workflow", label=" APPLY", color="#2E7D32", fontcolor="#2E7D32")
    dot.edge("strategy_agent", "firestore", label=" SKIP / dealbreaker", color="#C62828", fontcolor="#C62828")

    dot.edge("resume_workflow", "ground_truth", label=" audit bullets")
    dot.edge("cover_letter_workflow", "ground_truth", label=" audit paragraphs")
    dot.edge("resume_workflow", "dispatch", label=" passed audit")
    dot.edge("cover_letter_workflow", "dispatch", label=" passed audit")
    dot.edge("dispatch", "firestore", label=" Drive doc links")

    dot.edge("firestore", "standup_summary")
    dot.edge("standup_summary", "email_notifier", label=" today's summary")
    dot.edge("standup_summary", "console_standup", label=" today's summary")

    # ------------------------------------------------------------------
    # Legend
    # ------------------------------------------------------------------
    with dot.subgraph(name="cluster_legend") as c:
        c.attr(label="Legend", style="rounded", color="#9E9E9E", fontname=FONT, fontsize="12")
        c.node("legend_profile", "Ground truth", fillcolor=COLOR_PROFILE, fontsize="9")
        c.node("legend_discovery", "Lead discovery source", fillcolor=COLOR_DISCOVERY, fontsize="9")
        c.node("legend_filter", "Filter / dedupe logic", fillcolor=COLOR_FILTER, fontsize="9")
        c.node("legend_agent", "LLM reasoning agent", fillcolor=COLOR_AGENT, fontsize="9")
        c.node("legend_decision", "Decision point", fillcolor=COLOR_DECISION, fontsize="9")
        c.node("legend_tool", "Deterministic tool / guardrail", fillcolor=COLOR_TOOL, fontsize="9")
        c.node("legend_store", "Persistence", shape="cylinder", fillcolor=COLOR_STORE, fontsize="9")
        c.node("legend_output", "Outbound notification", fillcolor=COLOR_OUTPUT, fontsize="9")
        c.edge("legend_profile", "legend_discovery", style="invis")
        c.edge("legend_discovery", "legend_filter", style="invis")
        c.edge("legend_filter", "legend_agent", style="invis")
        c.edge("legend_agent", "legend_decision", style="invis")
        c.edge("legend_decision", "legend_tool", style="invis")
        c.edge("legend_tool", "legend_store", style="invis")
        c.edge("legend_store", "legend_output", style="invis")

    output_path = dot.render("career_os_workflow_graph", cleanup=True)
    print(f"Workflow graph successfully saved to: {output_path}")


if __name__ == "__main__":
    draw_career_os_workflow()
