"""
YC Launch Monitor — Configuration
Loads from .env, validates required fields, provides defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip() or default


# ── Slack ──────────────────────────────────────────────
SLACK_BOT_TOKEN: str = _require("SLACK_BOT_TOKEN")
SLACK_CHANNEL_ID: str = _require("SLACK_CHANNEL_ID")

# ── X / Twitter (v2 API) ──────────────────────────────
TWITTER_BEARER_TOKEN: str = _optional("TWITTER_BEARER_TOKEN")

# ── LinkedIn ───────────────────────────────────────────
LINKEDIN_ACCESS_TOKEN: str = _optional("LINKEDIN_ACCESS_TOKEN")

# ── Monitoring ─────────────────────────────────────────
CHECK_INTERVAL_HOURS: int = int(_optional("CHECK_INTERVAL_HOURS", "8"))
STATE_DB_PATH: str = _optional("STATE_DB_PATH", str(BASE_DIR / "state.jsonl"))
LOG_LEVEL: str = _optional("LOG_LEVEL", "INFO")

# ── YC Sources ─────────────────────────────────────────
YC_DIRECTORY_URL = "https://www.ycombinator.com/companies"
YC_SPEEDRUN_URL = "https://www.ycombinator.com/companies?batch=Speedrun"
