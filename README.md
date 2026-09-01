# YC Launch Monitor

Real-time monitoring of YC/Speedrun company launches across the YC directory, X/Twitter, and LinkedIn — with instant Slack alerts.

## Features

- **YC Directory scraping** — detects new companies the moment they appear on ycombinator.com
- **X/Twitter monitoring** — searches for founder launch posts via the Twitter API v2
- **LinkedIn monitoring** — scrapes the #ycombinator hashtag for founder announcements
- **Slack alerts** — rich Block Kit messages with company name, batch, description, and links
- **Crash-safe state store** — append-only JSONL file with file-locking; never loses data
- **Graceful shutdown** — handles SIGINT/SIGTERM cleanly
- **CLI flags** — `--once`, `--interval`, `--test-slack`, `--status`

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.10+ | Uses `X \| Y` union type syntax |
| Slack workspace | — | Need permission to create apps |
| Twitter API | v2 | Free tier works (recent search endpoint) |
| LinkedIn | — | Works without API (RSS/scraping fallback) |

## Setup Guide

### 1. Clone the repository

```bash
git clone <your-repo-url> yc-launch-monitor
cd yc-launch-monitor
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"** → **"From scratch"**
3. Name it `YC Launch Monitor`, pick your workspace
4. Under **OAuth & Permissions**, add these **Bot Token Scopes**:
   - `chat:write` — send messages
   - `channels:read` — list channels (to verify the target channel)
5. Click **"Install to Workspace"** → **"Authorize"**
6. Copy the **Bot User OAuth Token** (starts with `xoxb-`)
7. Open the target Slack channel, click the channel name → **"Integrations"** → **"Add an app"** → add your new app
8. Copy the **Channel ID** (right-click channel name → "Copy link" — the ID is the last part)

### 4. Get Twitter API Token

1. Go to [developer.twitter.com](https://developer.twitter.com)
2. Create a project and app (Free tier is sufficient)
3. Under **Keys and Tokens**, generate a **Bearer Token**
4. Copy the token

### 5. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your tokens:

```env
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_CHANNEL_ID=C0123456789
TWITTER_BEARER_TOKEN=your-twitter-bearer-token
# Optional:
# LINKEDIN_ACCESS_TOKEN=your-linkedin-access-token
CHECK_INTERVAL_HOURS=8
STATE_DB_PATH=./state.jsonl
LOG_LEVEL=INFO
```

### 6. Test the Slack connection

```bash
python run.py --test-slack
```

You should see a test message appear in your Slack channel.

### 7. Run the monitor

```bash
# Single check
python run.py --once

# Continuous monitoring (default: every 8 hours)
python run.py

# Custom interval
python run.py --interval 4
```

## Configuration Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SLACK_BOT_TOKEN` | ✅ | — | Slack Bot OAuth token (`xoxb-…`) |
| `SLACK_CHANNEL_ID` | ✅ | — | Target Slack channel ID |
| `TWITTER_BEARER_TOKEN` | ❌ | `""` | Twitter API v2 bearer token |
| `LINKEDIN_ACCESS_TOKEN` | ❌ | `""` | LinkedIn API token (optional — scraping works without it) |
| `CHECK_INTERVAL_HOURS` | ❌ | `8` | Hours between monitoring cycles |
| `STATE_DB_PATH` | ❌ | `./state.jsonl` | Path to the JSONL state file |
| `LOG_LEVEL` | ❌ | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## CLI Flags

| Flag | Description |
|------|-------------|
| `--once` | Run a single check cycle and exit |
| `--interval N` | Override check interval to N hours |
| `--test-slack` | Send a test message to Slack and exit |
| `--status` | Show state store statistics |
| `--log-level LEVEL` | Override log verbosity |

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                     run.py                          │
│              CLI parsing, startup,                  │
│           graceful shutdown, main loop              │
└──────────┬──────────────────────────┬───────────────┘
           │                          │
           ▼                          ▼
┌─────────────────────┐    ┌──────────────────────────┐
│   sources/           │    │    slack_transport        │
│                      │    │   (inline in run.py)      │
│  ┌────────────────┐  │    │                          │
│  │ yc_directory   │──┤    │  Formats Block Kit       │
│  │  (directory     │  │    │  messages, sends via     │
│  │   scraping)     │  │    │  Slack Web API           │
│  └────────────────┘  │    └────────────┬─────────────┘
│  ┌────────────────┐  │                 │
│  │ twitter_monitor│──┤                 ▼
│  │  (API v2       │  │         ┌──────────────┐
│  │   search)      │  │         │   Slack       │
│  └────────────────┘  │         │   Channel     │
│  ┌────────────────┐  │         └──────────────┘
│  │ linkedin_monitor│──┤
│  │  (hashtag       │  │
│  │   scraping)     │  │
│  └────────────────┘  │
└──────────┬───────────┘
           │
           ▼
┌─────────────────────┐
│   state_store        │
│   (JsonlStore)       │
│                      │
│  Append-only JSONL   │
│  File-locked writes  │
│  Crash-safe          │
└─────────────────────┘
```

**Module overview:**

| Module | Purpose |
|--------|---------|
| `run.py` | Entry point: CLI parsing, `--once`/`--test-slack`/`--status`, graceful shutdown |
| `config.py` | Loads `.env`, validates required vars, exports typed constants |
| `monitor.py` | Orchestrator: polls all sources → dedups → sends enriched Slack alerts |
| `sources/yc_directory.py` | New YC listings via the public `yc-oss` change feed (no API key) |
| `sources/speedrun.py` | a16z Speedrun companies via public REST API (no API key, baseline on first run) |
| `sources/targeting.py` | Builds a deep per-alert targeting brief (category, ICP relevance, outreach angle) |
| `sources/twitter_monitor.py` | X/Twitter founder posts — API v2 or pluggable keyword monitor |
| `sources/linkedin_monitor.py` | LinkedIn founder posts — pluggable public scraper adapter |
| `slack_transport.py` | Slack Block Kit payloads (value summary + source cross-reference) + send with retries |
| `state_store.py` | Crash-safe append-only JSONL store with file-locking (dedup) |
| `scheduler.py` | Persistent task queue with at-least-once delivery (crash recovery) |

## Data sources: what works with no keys

| Source | Method | Needs a key? |
|--------|--------|--------------|
| YC Directory | `yc-oss` GitHub Pages change feed (`changes/latest.json`) | **No** |
| a16z Speedrun | public REST API `speedrun-api.a16z.com` | **No** |
| X / Twitter | official API v2, or any third-party keyword monitor via pluggable adapter | **Yes / optional** |
| LinkedIn | pluggable adapter to a public scraper (e.g. Apify no-cookie) | **Yes / optional** |

YC Directory + Speedrun work out of the box, so the bot is fully functional
before you add any API keys. X/LinkedIn are gracefully skipped until
configured.

## Usage Examples

```bash
# Check once and see what's new
python run.py --once

# Run in background with cron-like 4-hour interval
python run.py --interval 4

# Debug mode — see every HTTP request and parse step
python run.py --once --log-level DEBUG

# Check what the state store has tracked
python run.py --status

# Send a test alert to verify Slack is configured
python run.py --test-slack
```

## Sample Slack Alert

When a new YC company is detected, you'll receive a rich Slack message like this:

```
┌──────────────────────────────────────────────────┐
│  🏢 New YC Company Detected                      │
├──────────────────────────────────────────────────┤
│  Name: Acme AI                                   │
│  Batch: S26                                      │
│                                                  │
│  AI-powered code review for enterprise teams.     │
│                                                  │
│  [ View on YC ]                                  │
└──────────────────────────────────────────────────┘
```

X/Twitter alerts show the author handle, post snippet, and a link to the tweet. LinkedIn alerts follow the same pattern with the author name and post preview.

## License

MIT
