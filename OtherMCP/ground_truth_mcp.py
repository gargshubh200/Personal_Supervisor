import json
import re
from pathlib import Path
from typing import Dict, Any, List, Set
from mcp.server.mcpserver import MCPServer

# Initialize FastMCP Server
mcp = MCPServer("GroundTruthServer")

DATA_PATH = Path(__file__).parent.parent / "master_profile.json"

# Commas inside numbers ("1,000") are stripped before tokenizing so they compare equal.
_THOUSANDS_SEPARATOR_REGEX = re.compile(r"(?<=\d),(?=\d)")
_NUMBER_TOKEN_REGEX = re.compile(r"\d+(?:\.\d+)?")


def _load_profile() -> Dict[str, Any]:
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_number_tokens(text: str) -> List[str]:
    if not text:
        return []
    return _NUMBER_TOKEN_REGEX.findall(_THOUSANDS_SEPARATOR_REGEX.sub("", text))


def _collect_strings(node: Any, out: List[str]) -> None:
    """Recursively gathers every string value in the profile (claims, bullets, dates, skills...)."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _collect_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_strings(value, out)


def _profile_number_tokens(profile: Dict[str, Any]) -> Set[str]:
    strings: List[str] = []
    _collect_strings(profile, strings)
    tokens: Set[str] = set()
    for s in strings:
        tokens.update(_extract_number_tokens(s))
    return tokens


@mcp.tool()
def verify_claim(claim_text: str, extra_allowed_text: str = "") -> Dict[str, Any]:
    """
    Validates that every number in a generated bullet/claim exists somewhere in
    master_profile.json. `extra_allowed_text` (e.g. the company name / role title)
    contributes additional allowed numbers so names like "1Password" or "Web3"
    don't trip the guardrail.
    """
    profile = _load_profile()
    allowed = _profile_number_tokens(profile) | set(_extract_number_tokens(extra_allowed_text))
    claim_numbers = _extract_number_tokens(claim_text)
    unverified = sorted({n for n in claim_numbers if n not in allowed})

    if unverified:
        return {
            "verified": False,
            "reason": f"Claim contains numbers not found in master_profile.json: {unverified}",
            "claim": claim_text,
            "unverified_numbers": unverified
        }

    return {
        "verified": True,
        "matched_verified_facts": sorted(set(claim_numbers))
    }


if __name__ == "__main__":
    mcp.run()
