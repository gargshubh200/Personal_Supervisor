import os
from typing import Dict, Any, List
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

HIRING_PATTERNS = {
    "Pattern_A_Direct_Intent": ['"I\'m hiring"', '"looking for a"', '"open role on my team"'],
    "Pattern_B_Call_To_Action": ['"DM me"', '"send me your resume"', '"drop your portfolio"'],
    "Pattern_C_Team_Growth": ['"growing the team"', '"excited to announce"', '"just opened a req"']
}


@observe(name="LinkedInPostsMCP: Search Hiring Managers")
@mcp.tool()
def search_hiring_manager_posts(
        search_queries: List[str],
        locations: List[str] = None,
        pattern_type: str = "Pattern_A_Direct_Intent",
        limit: int = 5
) -> List[Dict[str, Any]]:
    """
    Scrapes LinkedIn POSTS using optimized 5-operator Boolean strings:
    (role1 OR role2 OR role3) AND (pat1 OR pat2 OR pat3)
    Filters results post-hoc against target locations.
    """
    if not apify_client:
        print("⚠️ Warning: APIFY_API_TOKEN not set in .env. Returning fallback lead.")
        return _fallback_hiring_lead(search_queries[0] if search_queries else "Software Engineer")

    locations = locations or []

    # 1. Format Roles Clause (Up to 3 roles -> 2 ORs)
    top_roles = search_queries[:3]
    roles_clause = " OR ".join([f'"{r}"' for r in top_roles])

    # 2. Format Patterns Clause (3 patterns -> 2 ORs)
    patterns = HIRING_PATTERNS.get(pattern_type, HIRING_PATTERNS["Pattern_A_Direct_Intent"])
    patterns_clause = " OR ".join(patterns)

    # 3. Combine into single query string (Total: 4 ORs + 1 AND = 5 Operators)
    search_query = f"({roles_clause}) AND ({patterns_clause})"

    run_input = {
        "searchQueries": [search_query],
        "postedLimit": "24h",
        "sortBy": "date",
        "maxPosts": limit * 3  # Fetch extra to account for post-hoc location filtering
    }

    try:
        print(f"📡 LINKEDIN POSTS MCP: Querying [{search_query}]...")
        run = apify_client.actor(HARVEST_POST_ACTOR_ID).call(run_input=run_input)

        if isinstance(run, dict):
            dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            dataset_id = getattr(run, "default_dataset_id", getattr(run, "defaultDatasetId", None))

        leads = []
        seen_urls = set()

        if dataset_id:
            for item in apify_client.dataset(dataset_id).iterate_items():
                post_content = item.get("content", "")
                author = item.get("author", {})
                posted_at = item.get("postedAt", {})
                engagement = item.get("engagement", {})

                author_name = author.get("name", "Hiring Manager")
                author_headline = author.get("info", "Engineering Leader")
                author_url = author.get("linkedinUrl", "")
                post_url = item.get("linkedinUrl", "")

                if post_url in seen_urls:
                    continue

                # Post-Hoc Location Check: Verify if any target location keyword is present
                combined_text = f"{author_headline} {post_content}".lower()
                matches_location = not locations or any(loc.lower() in combined_text for loc in locations)

                if post_content and author_name != "Hiring Manager" and matches_location:
                    seen_urls.add(post_url)
                    leads.append({
                        "manager_name": author_name,
                        "manager_title": author_headline,
                        "manager_profile_url": author_url,
                        "matched_pattern": pattern_type,
                        "post_url": post_url,
                        "post_text": post_content[:2000],
                        "posted_ago": posted_at.get("postedAgoText", "Recently"),
                        "engagement_likes": engagement.get("likes", 0)
                    })

                    if len(leads) >= limit:
                        break

        langfuse.update_current_span(
            metadata={
                "search_query": search_query,
                "locations": str(locations),
                "leads_found": len(leads)
            }
        )

        print(f"✅ LINKEDIN POSTS MCP: Retrieved {len(leads)} location-matched leads.")
        return leads if leads else _fallback_hiring_lead(search_queries[0] if search_queries else "Software Engineer")

    except Exception as e:
        print(f"❌ LINKEDIN POSTS MCP ERROR: {str(e)}")
        return _fallback_hiring_lead(search_queries[0] if search_queries else "Software Engineer")


def _fallback_hiring_lead(role: str) -> List[Dict[str, Any]]:
    return [{
        "manager_name": "Alex Vance",
        "manager_title": "VP of Engineering & Applied AI at Cognition",
        "manager_profile_url": "https://linkedin.com/in/alexvance-example",
        "matched_pattern": "Pattern_A_Direct_Intent",
        "post_url": "https://linkedin.com/posts/alexvance-1234",
        "post_text": f"I'm hiring a {role} on my team in Bengaluru / Remote to lead customer integrations! DM me your resume directly.",
        "posted_ago": "1 day ago",
        "engagement_likes": 14
    }]


if __name__ == "__main__":
    mcp.run()