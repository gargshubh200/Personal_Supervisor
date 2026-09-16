"""
config_loader.py

Loads config.yaml (repo root) once and exposes it as a cached dict, plus a
few typed convenience getters for the most commonly used sections. Every
MCP/SubAgent that needs setup data (toggles, search queries, locations,
company lists, career strategy constraints) should import from here instead
of hardcoding its own copy.

Resolution is relative to this file's location, so it works regardless of
the caller's current working directory.
"""

import functools
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).parent
CONFIG_PATH = REPO_ROOT / "config.yaml"


@functools.lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    """Loads and caches config.yaml. Raises FileNotFoundError if missing."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {CONFIG_PATH}. This file holds all "
            f"runtime setup data (toggles, search terms, target companies, etc.) "
            f"and must be present at the repo root."
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_mcp_toggles() -> Dict[str, bool]:
    return load_config()["mcp_toggles"]


def get_master_search_queries() -> List[str]:
    return load_config()["search"]["master_search_queries"]


def get_master_locations() -> List[str]:
    return load_config()["search"]["master_locations"]


def get_default_locations() -> List[str]:
    return load_config()["search"]["default_locations"]


def get_india_location_keywords() -> List[str]:
    return load_config()["search"]["india_location_keywords"]


def get_hiring_post_roles() -> List[str]:
    return load_config()["search"]["hiring_post_roles"]


def get_career_strategy_constraints() -> Dict[str, Any]:
    return load_config()["career_strategy_constraints"]


def get_ats_target_companies() -> List[Dict[str, str]]:
    return load_config()["ats_target_companies"]
