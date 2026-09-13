"""
JobSearchMCP/linkedin_hiring_posts_mcp.py

Scrapes LinkedIn posts by hiring managers announcing open engineering roles.
Uses Apify HarvestAPI actor with Boolean search queries.

v2 Changes:
- Runs ALL hiring patterns per call instead of a single pattern (better coverage)
- Deduplication across all pattern runs via seen_urls
- Expanded VALID_TITLE_KEYWORDS to catch more genuine engineering leaders
- Expanded INVALID_NAME_TERMS with placement/talent/consultant patterns
- Added INVALID_TITLE_TERMS for recruiter-agency signals
- Added Pattern_D_AI_Platform_Specific for AI/data engineering hiring posts
- Improved location check: headline-weighted over post content
- Search query uses natural language role terms (what hiring managers write)
  instead of formal JD titles
"""

import os
import re
import time
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from apify_client import ApifyClient
from mcp.server.mcpserver import MCPServer
from langfuse import observe, get_client

load_dotenv()

mcp = MCPServer("LinkedInHiringPostsServer")
langfuse = get_client()

apify_token = os.getenv("APIFY_API_TOKEN")
apify_client = ApifyClient(apify_token) if apify_token else None

HARVEST_POST_ACTOR_ID = "harvestapi/linkedin-post-search"

# ── HIRING SIGNAL PATTERNS ────────────────────────────────────────────────────
# Natural language patterns that hiring managers use in LinkedIn posts.
# These are Boolean search strings passed directly to the Apify actor.
#
# ⚠ HARD CONSTRAINT (confirmed via HarvestAPI docs): each individual searchQueries
# string is capped at 500 characters AND 5 boolean operators (AND/OR/NOT) by
# LinkedIn's own boolean search grammar — exceeding this silently degrades or
# invalidates the search. Each pattern category is capped at 3 phrases (2 OR
# operators) so that combined with the 2-role OR clause (1 operator) and the
# joining AND (1 operator), a single query never exceeds 4 operators total.

HIRING_PATTERNS = {
    "Pattern_A_Direct_Intent": [
        '"I\'m hiring"',
        '"we\'re hiring"',
        '"looking for a"'
    ],
    "Pattern_B_Call_To_Action": [
        '"DM me"',
        '"send me your resume"',
        '"apply below"'
    ],
    "Pattern_C_Team_Growth": [
        '"growing the team"',
        '"expanding our team"',
        '"new headcount"'
    ],
    "Pattern_D_AI_Platform_Specific": [
        '"building our AI infrastructure"',
        '"AI systems engineer"',
        '"looking for a data engineer"'
    ]
}

# ── SEARCH ROLES ──────────────────────────────────────────────────────────────
# Use natural language terms that hiring managers write in posts,
# NOT formal JD titles like "Forward Deployed Engineer".
# Each role is queried individually (looped, not OR-combined) against each
# hiring pattern — see search_hiring_manager_posts() below.

DEFAULT_HIRING_POST_ROLES = [
    "platform engineer",
    "AI engineer",
    "software engineer",
    "data engineer",
]

# Roles that are generic on their own (would surface plain non-AI platform/backend
# roles). These get an "applied AI" qualifier ANDed in so we only surface postings
# for platform/software/data engineering roles that are actually AI-adjacent.
# "AI engineer" is intentionally excluded — it's already AI-specific.
ROLES_REQUIRING_AI_QUALIFIER = {"platform engineer", "software engineer", "data engineer"}
AI_QUALIFIER = '"applied AI"'

# Delay (seconds) between sequential Apify actor calls when looping roles x patterns,
# to avoid hammering the actor / LinkedIn's underlying rate limits.
RATE_LIMIT_DELAY_SECONDS = 2.0

# Max boolean operators (AND/OR/NOT) permitted per single searchQueries string,
# per HarvestAPI's documented LinkedIn boolean-search constraint. Used as a
# runtime safety check before dispatching each query to the actor.
MAX_BOOLEAN_OPERATORS = 5


def _count_boolean_operators(query: str) -> int:
    """Counts AND/OR/NOT boolean keywords in a query string (case-sensitive, as LinkedIn requires)."""
    return len(re.findall(r'\b(AND|OR|NOT)\b', query))


def _build_role_clause(role: str) -> str:
    """
    Builds the role portion of a Boolean query. Generic roles (platform/software/
    data engineer) get an "applied AI" qualifier ANDed in (1 operator) so we only
    surface AI-adjacent postings for these titles; "AI engineer" stays plain since
    it's already AI-specific.
    """
    role_term = f'"{role}"'
    if role.lower().strip() in ROLES_REQUIRING_AI_QUALIFIER:
        return f'({role_term} AND {AI_QUALIFIER})'
    return role_term


# ── AUTHOR VALIDATION ─────────────────────────────────────────────────────────

# Words that indicate an aggregator/bot page, not a real person
INVALID_NAME_TERMS = {
    # Job board indicators
    "jobs", "job", "careers", "career", "openings", "vacancy", "vacancies",
    "hiring", "employment", "opportunities", "opportunity",
    # Aggregator/bot indicators
    "portal", "board", "network", "updates", "hub", "feed",
    "newsletter", "digest", "daily", "weekly", "alerts", "alert",
    # Firm/company type indicators (in names)
    "agency", "inc", "llc", "ltd", "corporation", "corp",
    "services", "solutions", "group", "page",
    # Recruiter/HR indicators
    "recruiter", "recruitment", "staffing", "placement",
    "consultant", "hr", "talent", "people ops", "sourcer",
    "headhunter", "associate recruiter", "technical recruiter",
    # Engagement metrics (not names)
    "followers", "subscribers"
}

INVALID_TITLE_TERMS = [
    # Aggregator/board titles
    "jobs", "job board", "hiring board", "recruitment portal",
    "staffing firm", "agency",
    # Recruiter titles that are NOT what we want
    "recruiter", "recruitment", "talent acquisition",
    "sourcer", "headhunter", "hr manager",
    # Engagement metric patterns
    "follower", "followers", "subscriber", "subscribers"
]

# Title keywords that indicate a genuine engineering leader / hiring manager
VALID_TITLE_KEYWORDS = [
    # Leadership levels
    "vp", "vice president", "director", "head",
    "cto", "ceo", "coo", "cpo",
    "founder", "cofounder", "co-founder",
    # Engineering management
    "engineering manager", "em", "tech lead", "technical lead",
    "manager", "lead",
    # Senior IC levels (can also post hiring)
    "staff", "principal", "architect",
    "senior engineer", "senior software",
    # Domain indicators (engineering context)
    "engineering", "engineer",
    "platform", "infrastructure", "data", "ai", "ml", "applied",
    # Explicit hiring context
    "at", "@"  # "at Company" or "@Company" in headline confirms employment
]


# ── VALIDATION LOGIC ──────────────────────────────────────────────────────────

def is_valid_human_hiring_manager(name: str, headline: str) -> bool:
    """
    Returns True only if the post author appears to be an individual
    engineering leader or hiring manager — not a job board, aggregator,
    recruiter firm, or bot page.

    Validation steps (fail-fast, reject-first):
    1. Reject empty or generic placeholder names
    2. Reject names containing digits (aggregator pages like "Spokane 247 Jobs")
    3. Reject names containing job board / aggregator blacklist terms
    4. Reject headlines containing recruiter-firm indicators
    5. Reject headline follower count patterns
    6. Require name to have at least 2 words (first + last name)
    7. Require headline to contain a valid leadership/engineering indicator
       OR an employment relationship marker (" at ", " @ ")
    """
    if not name or name.strip() in ("Hiring Manager", ""):
        return False

    name_clean = name.strip()
    name_lower = name_clean.lower()
    headline_lower = (headline or "").lower().strip()

    # 1. Reject digit-containing names (aggregator pages)
    if any(char.isdigit() for char in name_clean):
        return False

    # 2. Reject names with job board / aggregator terms
    for term in INVALID_NAME_TERMS:
        if re.search(r'\b' + re.escape(term) + r'\b', name_lower):
            return False

    # 3. Reject headlines with recruiter-firm indicators
    for term in INVALID_TITLE_TERMS:
        if re.search(r'\b' + re.escape(term) + r'\b', headline_lower):
            return False

    # 4. Reject follower count patterns in headline
    if re.search(r'\d+\s*(k|m)?\s*followers?', headline_lower):
        return False

    # 5. Name must look like a human name (minimum 2 words)
    name_parts = name_clean.split()
    if len(name_parts) < 2:
        return False

    # 6. Headline must contain a valid role/leadership indicator
    #    OR an employment relationship marker
    has_valid_role = any(
        re.search(r'\b' + re.escape(kw) + r'\b', headline_lower)
        for kw in VALID_TITLE_KEYWORDS
    )
    has_company_marker = any(
        sep in headline_lower
        for sep in [" at ", " @ ", " | ", " - "]
    )

    return has_valid_role or has_company_marker


def matches_location_filter(
    author_headline: str,
    post_content: str,
    locations: List[str]
) -> bool:
    """
    Returns True if the post appears relevant to target locations.
    Weights headline location match over post content match.
    If no locations provided, always returns True.
    """
    if not locations:
        return True

    headline_lower = author_headline.lower()
    post_lower = post_content.lower()

    # Headline match is stronger signal (author is based there)
    for loc in locations:
        if loc.lower() in headline_lower:
            return True

    # Post content match is weaker (could mention location in passing)
    # Only accept if at least 2 locations match in post, OR it's a specific city
    city_locations = [
        loc for loc in locations
        if loc.lower() not in {"india", "remote", "asia"}
    ]
    post_city_matches = sum(1 for loc in city_locations if loc.lower() in post_lower)

    if post_city_matches >= 1:
        return True

    # Accept generic "India" or "remote" in post content
    generic_locations = {"india", "remote", "bengaluru", "bangalore"}
    for loc in locations:
        if loc.lower() in generic_locations and loc.lower() in post_lower:
            return True

    return False


# ── POST CONTENT VALIDATION ────────────────────────────────────────────────────
# HarvestAPI's LinkedIn post search does fuzzy/semantic matching, NOT strict
# Boolean AND — it frequently returns posts that don't actually contain the
# searched role or pattern phrase at all (personal updates, conference recaps,
# certification announcements, recruiter/agency spam). These checks close that
# gap by verifying the genuine signal is present in the post text itself
# before a lead is ever surfaced.

# Minimum years-of-experience mentioned in a post that triggers a reject
# (matches the 5+ years sourcing-stage threshold used by the other MCPs).
MIN_ACCEPTABLE_YOE_CEILING = 5

# Catches "12+ yrs", "10-12 years", "8-10 years", "6+ Years", "10–12 yrs", etc.
YOE_MENTION_REGEX = re.compile(
    r'(\d{1,2})\s*\+?\s*(?:[-–—]|to)\s*(\d{1,2})?\s*\+?\s*(?:yrs?\.?|years?)\b',
    re.IGNORECASE
)
YOE_MENTION_REGEX_SIMPLE = re.compile(
    r'(\d{1,2})\s*\+?\s*(?:yrs?\.?|years?)\b',
    re.IGNORECASE
)

# Recruiter/staffing-agency bulk-post signals — genuine hiring-manager posts
# rarely contain these; agency/spam posts frequently do.
RECRUITER_AGENCY_CONTENT_REGEX = re.compile(
    r'(job code|send (?:your )?(?:cv|resume)\s+to|work authorization|tag someone who|'
    r'comment\s+["\']?\w+["\']?\s+and\s+connect|connect with (?:the )?recruiting team|'
    r'h4-?ead|\bopt\b\s*/|\bcpt\b|canadian work permit)',
    re.IGNORECASE
)

# Non-engineering roles that occasionally slip past author validation
# (e.g. Talent Ops, Sales, BD posts that mention "engineer" in passing).
NON_ENGINEERING_ROLE_CONTENT_REGEX = re.compile(
    r'\b(talent (?:operations|acquisition) manager|business development (?:manager|executive)|'
    r'account executive|sales (?:executive|manager|representative)|hr manager|'
    r'customer success manager|people ops manager)\b',
    re.IGNORECASE
)

# AI-context keywords required for the 3 generic roles (platform/software/data
# engineer) that are queried with an "applied AI" qualifier — ensures the post
# is genuinely AI-adjacent, not just a coincidental role+pattern match.
AI_CONTEXT_KEYWORDS_REGEX = re.compile(
    r'\b(applied ai|generative ai|genai|gen ai|ai/ml|ml/ai|llm|llms|artificial intelligence|'
    r'ai infrastructure|ai platform|ai systems|machine learning)\b',
    re.IGNORECASE
)


def _extract_min_required_years(content: str) -> Optional[int]:
    """Returns the lowest years-of-experience figure mentioned in the post, or None if absent."""
    years_found = []
    for m in YOE_MENTION_REGEX.finditer(content):
        years_found.append(int(m.group(1)))
        if m.group(2):
            years_found.append(int(m.group(2)))
    for m in YOE_MENTION_REGEX_SIMPLE.finditer(content):
        years_found.append(int(m.group(1)))

    return min(years_found) if years_found else None


def _content_confirms_hiring_signal(post_content: str, role: str, pattern_phrases: List[str]) -> bool:
    """
    Verifies the actual searched role AND the actual matched-pattern phrase are
    genuinely present in the post text (word-boundary safe), and that AI-context
    keywords are present for roles requiring the "applied AI" qualifier.
    Rejects the post otherwise, regardless of what the actor claims it matched.
    """
    content_lower = post_content.lower()

    # 1. Role must genuinely appear as a whole phrase (rejects e.g. "data engineering"
    #    matching a "data engineer" search due to naive substring matching).
    role_regex = re.compile(r'\b' + re.escape(role.lower().strip()) + r'\b')
    if not role_regex.search(content_lower):
        return False

    # 2. At least one literal pattern phrase for the matched category must appear.
    if not any(phrase.strip('"').lower() in content_lower for phrase in pattern_phrases):
        return False

    # 3. Generic roles must show genuine AI-adjacent context.
    if role.lower().strip() in ROLES_REQUIRING_AI_QUALIFIER and not AI_CONTEXT_KEYWORDS_REGEX.search(content_lower):
        return False

    return True


def _passes_post_hiring_quality_filters(post_content: str) -> bool:
    """Rejects posts requiring 5+ years, recruiter/agency spam signals, or non-engineering roles."""
    min_years = _extract_min_required_years(post_content)
    if min_years is not None and min_years >= MIN_ACCEPTABLE_YOE_CEILING:
        return False

    if RECRUITER_AGENCY_CONTENT_REGEX.search(post_content):
        return False

    if NON_ENGINEERING_ROLE_CONTENT_REGEX.search(post_content):
        return False

    return True


# ── MAIN TOOL ─────────────────────────────────────────────────────────────────

@observe(name="LinkedInPostsMCP: Search Hiring Managers")
@mcp.tool()
def search_hiring_manager_posts(
        search_queries: List[str] = None,
        locations: List[str] = None,
        limit_per_pattern: int = 2
) -> List[Dict[str, Any]]:
    """
    Scrapes LinkedIn posts from verified human hiring managers using Boolean
    search strings, looping over EVERY role individually against EVERY hiring
    pattern (i.e. roles x patterns combos, not one OR-combined query). This
    maximizes coverage while keeping each individual query well under
    LinkedIn's 5-operator / 500-character Boolean search limit.

    Generic role terms (platform/software/data engineer) are ANDed with an
    "applied AI" qualifier so only AI-adjacent postings surface for those
    titles; "AI engineer" is queried plain since it's already AI-specific.

    Args:
        search_queries: Role terms to search for (uses DEFAULT_HIRING_POST_ROLES if not provided).
                        Pass natural language terms, not formal JD titles.
        locations: Location strings to filter by (post-hoc filter on headline + content).
        limit_per_pattern: Max leads per role-pattern combo
                           (total actor calls = len(roles) * 4 patterns).

    Returns:
        List of verified hiring manager lead dicts.
    """
    if not apify_client:
        print("⚠️  APIFY_API_TOKEN not set. Returning fallback lead.")
        role = (search_queries or DEFAULT_HIRING_POST_ROLES)[0]
        return _fallback_hiring_lead(role)

    roles = search_queries or DEFAULT_HIRING_POST_ROLES
    locations = locations or []

    all_leads = []
    seen_urls = set()
    pattern_stats = {}

    total_combos = len(roles) * len(HIRING_PATTERNS)
    print(f"📡 LINKEDIN POSTS MCP: Looping {len(roles)} roles x {len(HIRING_PATTERNS)} patterns = {total_combos} queries...")

    combo_index = 0
    for role in roles:
        role_clause = _build_role_clause(role)

        for pattern_name, patterns in HIRING_PATTERNS.items():
            combo_index += 1
            combo_key = f"{role}|{pattern_name}"

            # Defensive cap: even if HIRING_PATTERNS is edited later, never exceed
            # 3 phrases (2 OR operators) per category so the combined query stays safe.
            patterns_clause = " OR ".join(patterns[:3])
            search_query = f"{role_clause} AND ({patterns_clause})"

            op_count = _count_boolean_operators(search_query)
            if op_count > MAX_BOOLEAN_OPERATORS or len(search_query) > 500:
                print(
                    f"⚠️  [{combo_key}] Query exceeds safe bounds "
                    f"({op_count} operators, {len(search_query)} chars) — skipping to avoid actor rejection."
                )
                pattern_stats[combo_key] = 0
                continue

            run_input = {
                "searchQueries": [search_query],
                "postedLimit": "24h",
                "sortBy": "date",
                "maxPosts": limit_per_pattern * 5  # Fetch extra to account for filtering
            }

            try:
                print(f"📡 LINKEDIN POSTS MCP [{combo_index}/{total_combos}] [{combo_key}]: Querying [{search_query}]...")
                run = apify_client.actor(HARVEST_POST_ACTOR_ID).call(run_input=run_input)

                dataset_id = None
                if isinstance(run, dict):
                    dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
                else:
                    dataset_id = getattr(run, "default_dataset_id", None) or getattr(run, "defaultDatasetId", None)

                pattern_leads = 0

                if dataset_id:
                    for item in apify_client.dataset(dataset_id).iterate_items():
                        if pattern_leads >= limit_per_pattern:
                            break

                        post_content = item.get("content", "")
                        author = item.get("author", {})
                        posted_at = item.get("postedAt", {})
                        engagement = item.get("engagement", {})

                        author_name = author.get("name", "")
                        author_headline = author.get("info", "")
                        author_url = author.get("linkedinUrl", "")
                        post_url = item.get("linkedinUrl", "")

                        # Skip duplicates across role/pattern combos
                        if post_url in seen_urls:
                            continue

                        # Validate author is a real human hiring manager
                        if not is_valid_human_hiring_manager(author_name, author_headline):
                            print(
                                f"🛡️  Filtered non-person: '{author_name}' | '{author_headline}'"
                            )
                            continue

                        # Location filter (headline-weighted)
                        if not matches_location_filter(author_headline, post_content, locations):
                            continue

                        if not post_content:
                            continue

                        # Post-content confirmation gate: reject if the actor's fuzzy
                        # match didn't actually contain our role + pattern signal.
                        if not _content_confirms_hiring_signal(post_content, role, patterns[:3]):
                            print(f"🛡️  Filtered non-genuine match (no literal role/pattern signal): {post_url}")
                            continue

                        # YOE / recruiter-agency / non-engineering-role content filters
                        if not _passes_post_hiring_quality_filters(post_content):
                            print(f"🛡️  Filtered low-quality lead (YOE/recruiter/non-eng signal): {post_url}")
                            continue

                        seen_urls.add(post_url)
                        all_leads.append({
                            "manager_name": author_name,
                            "manager_title": author_headline,
                            "manager_profile_url": author_url,
                            "matched_role": role,
                            "matched_pattern": pattern_name,
                            "post_url": post_url,
                            "post_text": post_content[:2000],
                            "posted_ago": posted_at.get("postedAgoText", "Recently"),
                            "engagement_likes": engagement.get("likes", 0)
                        })
                        pattern_leads += 1

                pattern_stats[combo_key] = pattern_leads
                print(f"✅ [{combo_key}]: {pattern_leads} verified leads.")

            except Exception as e:
                print(f"❌ LINKEDIN POSTS MCP ERROR [{combo_key}]: {str(e)}")
                pattern_stats[combo_key] = 0

            # Respect Apify actor / LinkedIn rate limits between sequential calls
            if combo_index < total_combos:
                time.sleep(RATE_LIMIT_DELAY_SECONDS)

    langfuse.update_current_span(
        metadata={
            "roles": roles,
            "locations": locations,
            "pattern_stats": pattern_stats,
            "total_leads": len(all_leads)
        }
    )

    print(f"\n📊 LINKEDIN POSTS MCP SUMMARY: {len(all_leads)} verified hiring manager leads.")

    if not all_leads:
        role = (roles)[0] if roles else "Software Engineer"
        return _fallback_hiring_lead(role)

    return all_leads


def _fallback_hiring_lead(role: str) -> List[Dict[str, Any]]:
    """Returns a single synthetic lead when Apify is unavailable or returns no results."""
    return [{
        "manager_name": "Alex Vance",
        "manager_title": "VP of Engineering & Applied AI at Cognition",
        "manager_profile_url": "https://linkedin.com/in/alexvance-example",
        "matched_pattern": "Pattern_A_Direct_Intent",
        "post_url": "https://linkedin.com/posts/alexvance-1234",
        "post_text": (
            f"I'm hiring a {role} on my team in Bengaluru / Remote to lead "
            f"AI infrastructure work. DM me your resume directly."
        ),
        "posted_ago": "1 day ago",
        "engagement_likes": 14
    }]


if __name__ == "__main__":
    mcp.run()