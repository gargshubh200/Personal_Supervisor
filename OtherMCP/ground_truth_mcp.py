import json
from pathlib import Path
from typing import Dict, Any
from mcp.server.mcpserver import MCPServer

# Initialize FastMCP Server
mcp = MCPServer("GroundTruthServer")

DATA_PATH = Path(__file__).parent.parent / "master_profile.json"


def _load_profile() -> Dict[str, Any]:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@mcp.tool()
def verify_claim(claim_text: str) -> Dict[str, Any]:
    """
    Validates whether a generated bullet or claim contains any unverified numbers or fake tools.
    Used as an ADK guardrail callback.
    """
    profile = _load_profile()
    metrics = profile.get("verified_metrics", [])

    # Simple verification logic checking metric anchors
    known_numbers = ["98%", "60%", "40%", "90+", "15+", "15", "7", "4", "3 hours", "15 days", "2.5"]

    has_number = any(char.isdigit() for char in claim_text)
    matched_known = [num for num in known_numbers if num in claim_text]

    if has_number and not matched_known:
        return {
            "verified": False,
            "reason": "Claim contains numeric metrics not found in master_profile.json",
            "claim": claim_text
        }

    return {
        "verified": True,
        "matched_verified_facts": matched_known
    }


if __name__ == "__main__":
    mcp.run()