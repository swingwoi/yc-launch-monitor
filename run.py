#!/usr/bin/env python3
"""
YC Launch Monitor — entry point.

Detects new YC/Speedrun companies across the YC directory, X/Twitter,
and LinkedIn, then sends alerts to Slack.

Usage:
    python run.py                 # run forever (default interval)
    python run.py --once          # single check, then exit
    python run.py --interval 4    # override check interval to 4 hours
    python run.py --test-slack    # send a test message and exit
    python run.py --status        # show state store statistics
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import NoReturn

# ── logging ──────────────────────────────────────────────────────────────────
log = logging.getLogger("yc-monitor")


def _banner() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(
        r"""
   _____ ____   ____
  / ____/ __ \ / __ \
 | |   | |  | | |  | |  _   _  __ _  __ _  ___  _ __
 | |   | |  | | |  | | | | | |/ _` |/ _` |/ _ \| '_ \
 | |___| |__| | |__| | | |_| | (_| | (_| | (_) | | | |
  \_____\____/ \____/   \__, |\__,_|\__, |\___/|_| |_|
                         __/ |      __/ |
                        |___/      |___/
    """
    )
    print(f"  YC Launch Monitor  •  started {now}")
    print()


# ── slack transport (inlined — no separate module needed) ────────────────────
def _send_slack_message(token: str, channel: str, text: str,
                        blocks=None) -> dict:
    """Post a message to Slack via the Web API. Returns the API response."""
    from slack_sdk import WebClient

    client = WebClient(token=token)
    kwargs: dict = {"channel": channel, "text": text}
    if blocks:
        kwargs["blocks"] = blocks
    return client.chat_postMessage(**kwargs)


def _test_slack(token: str, channel: str) -> None:
    """Send a test alert to Slack and print the result."""
    print("[test-slack] Sending test message …")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text",
                     "text": "🚀 YC Launch Monitor — Test Alert",
                     "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "If you can see this, the Slack integration is *working*.\n"
                    f"`channel`: `{channel}`  •  "
                    f"`time`: {datetime.now(timezone.utc).isoformat()}"
                ),
            },
        },
    ]
    resp = _send_slack_message(token, channel, "YC Launch Monitor — test alert", blocks)
    ok = resp.get("ok", False)
    ts = resp.get("ts", "?")
    print(f"[test-slack] {'✅ Sent' if ok else '❌ Failed'}  (ts={ts})")
    if not ok:
        err = resp.get("error", "unknown")
        print(f"[test-slack] Error: {err}", file=sys.stderr)
        sys.exit(1)


# ── status display ───────────────────────────────────────────────────────────
def _show_status(state_db: str) -> None:
    """Print state store statistics."""
    from state_store import JsonlStore

    store = JsonlStore(state_db)
    records = store.all()
    total = len(records)

    # Compute last check time from records
    last_check = None
    for rec in records:
        ts = rec.get("checked_at") or rec.get("updated_at") or rec.get("first_seen")
        if ts and (last_check is None or ts > last_check):
            last_check = ts

    # Breakdown by source
    by_source: dict[str, int] = {}
    for rec in records:
        src = rec.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1

    print("═══════════════════════════════════════════")
    print("  YC Launch Monitor — State Store Stats")
    print("═══════════════════════════════════════════")
    print(f"  Total companies tracked : {total}")
    print(f"  State file              : {state_db}")
    if last_check:
        print(f"  Last check              : {last_check}")
    else:
        print("  Last check              : (no data)")
    if by_source:
        print()
        print("  By source:")
        for src, cnt in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {src:20s} : {cnt}")
    print("═══════════════════════════════════════════")


# ── Monitor (ties sources → state → slack) ──────────────────────────────────
class Monitor:
    """
    One monitoring cycle:
      1. Scrape YC directory → new companies
      2. Search X/Twitter for YC founder posts
      3. Scrape LinkedIn for YC founder posts
      4. Diff against state store → alert on new findings
    """

    def __init__(self, state_db: str, slack_token: str, slack_channel: str,
                 twitter_token: str = "", linkedin_token: str = "",
                 interval_hours: int = 8):
        from state_store import JsonlStore
        self.state = JsonlStore(state_db)
        self.slack_token = slack_token
        self.slack_channel = slack_channel
        self.twitter_token = twitter_token
        self.linkedin_token = linkedin_token
        self.interval_hours = interval_hours
        self._running = True

    # ── public ───────────────────────────────────────────────────────────────

    def run_forever(self) -> None:
        """Run monitoring cycles in a loop until shutdown."""
        log.info("Starting continuous monitor (interval=%dh)", self.interval_hours)
        while self._running:
            self._cycle()
            if not self._running:
                break
            log.info("Sleeping %dh until next check …", self.interval_hours)
            self._interruptible_sleep(self.interval_hours * 3600)
        log.info("Monitor stopped.")

    def run_cycle(self) -> None:
        """Run exactly one monitoring cycle."""
        log.info("Running single check cycle")
        self._cycle()
        log.info("Single cycle complete.")

    def shutdown(self) -> None:
        self._running = False

    # ── internals ────────────────────────────────────────────────────────────

    def _cycle(self) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        new_alerts: list[dict] = []

        # 1) YC Directory
        new_alerts.extend(self._check_yc_directory(now_iso))

        # 2) X / Twitter
        if self.twitter_token:
            new_alerts.extend(self._check_twitter(now_iso))
        else:
            log.debug("Twitter token not set — skipping")

        # 3) LinkedIn
        new_alerts.extend(self._check_linkedin(now_iso))

        # 4) Send alerts
        if new_alerts:
            self._send_alerts(new_alerts)
        else:
            log.info("No new findings this cycle.")

    def _check_yc_directory(self, now_iso: str) -> list[dict]:
        from sources.yc_directory import check_new_yc_companies

        known = {r["slug"] for r in self.state.all() if "slug" in r}
        try:
            new_cos = check_new_yc_companies(known)
        except Exception as e:
            log.error("YC directory check failed: %s", e)
            return []

        alerts = []
        for co in new_cos:
            rec = {
                **co.to_dict(),
                "source": "yc_directory",
                "first_seen": now_iso,
            }
            self.state.append(rec)
            alerts.append({"type": "yc_new_company", "company": co.to_dict()})
            log.info("New YC company: %s (%s)", co.name, co.batch)
        return alerts

    def _check_twitter(self, now_iso: str) -> list[dict]:
        from sources.twitter_monitor import search_yc_posts, detect_early_founders

        known_slugs = {r["slug"] for r in self.state.all() if "slug" in r}
        last_id = None
        for r in reversed(self.state.all()):
            if r.get("source") == "twitter" and r.get("tweet_id"):
                last_id = r["tweet_id"]
                break

        try:
            posts = search_yc_posts(self.twitter_token, since_id=last_id)
            early = detect_early_founders(posts, known_slugs)
        except Exception as e:
            log.error("Twitter check failed: %s", e)
            return []

        alerts = []
        for post in early:
            rec = {**post.to_dict(), "first_seen": now_iso}
            self.state.append(rec)
            alerts.append({"type": "twitter_founder", "post": post.to_dict()})
            log.info("Twitter founder: @%s", post.author_handle)
        return alerts

    def _check_linkedin(self, now_iso: str) -> list[dict]:
        from sources.linkedin_monitor import (
            scrape_linkedin_hashtag, detect_yc_founders_linkedin)

        known_slugs = {r["slug"] for r in self.state.all() if "slug" in r}
        try:
            posts = scrape_linkedin_hashtag()
            matches = detect_yc_founders_linkedin(posts, known_slugs)
        except Exception as e:
            log.error("LinkedIn check failed: %s", e)
            return []

        alerts = []
        for post in matches:
            rec = {**post.to_dict(), "first_seen": now_iso}
            self.state.append(rec)
            alerts.append({"type": "linkedin_founder", "post": post.to_dict()})
            log.info("LinkedIn founder: %s", post.author_name)
        return alerts

    def _send_alerts(self, alerts: list[dict]) -> None:
        """Format alerts and send to Slack."""
        for alert in alerts:
            blocks = self._format_alert(alert)
            text = alert.get("type", "yc_alert")
            try:
                _send_slack_message(
                    self.slack_token, self.slack_channel, text, blocks)
                log.info("Alert sent: %s", alert["type"])
            except Exception as e:
                log.error("Slack send failed: %s", e)

    @staticmethod
    def _format_alert(alert: dict) -> list[dict]:
        alert_type = alert["type"]

        if alert_type == "yc_new_company":
            co = alert["company"]
            return [
                {
                    "type": "header",
                    "text": {"type": "plain_text",
                             "text": "🏢 New YC Company Detected",
                             "emoji": True},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Name:*\n{co['name']}"},
                        {"type": "mrkdwn", "text": f"*Batch:*\n{co['batch']}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn",
                             "text": co.get("description", "—")},
                },
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View on YC"},
                        "url": co["url"],
                    }],
                },
            ]

        if alert_type == "twitter_founder":
            post = alert["post"]
            snippet = post["text"][:200] + ("…" if len(post["text"]) > 200 else "")
            return [
                {
                    "type": "header",
                    "text": {"type": "plain_text",
                             "text": "🐦 YC Founder Post on X",
                             "emoji": True},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn",
                         "text": f"*Author:*\n{post['author_name']} (@{post['author_handle']})"},
                        {"type": "mrkdwn",
                         "text": f"*Posted:*\n{post['created_at']}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"> {snippet}"},
                },
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View on X"},
                        "url": post["url"],
                    }],
                },
            ]

        if alert_type == "linkedin_founder":
            post = alert["post"]
            snippet = post["text"][:200] + ("…" if len(post["text"]) > 200 else "")
            return [
                {
                    "type": "header",
                    "text": {"type": "plain_text",
                             "text": "💼 YC Founder Post on LinkedIn",
                             "emoji": True},
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn",
                         "text": f"*Author:*\n{post['author_name']}"},
                        {"type": "mrkdwn",
                         "text": f"*Posted:*\n{post.get('posted_at', '—')}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"> {snippet}"},
                },
                {
                    "type": "actions",
                    "elements": [{
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View on LinkedIn"},
                        "url": post.get("url", "#"),
                    }],
                },
            ]

        # Fallback
        return [
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": f"*Alert:* `{alert_type}`\n```{alert}```"}},
        ]

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that responds to shutdown signals."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))


# ── CLI ──────────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="yc-launch-monitor",
        description="Monitor YC directory, X, and LinkedIn for new company launches.",
    )
    p.add_argument("--once", action="store_true",
                   help="Run a single check cycle then exit")
    p.add_argument("--interval", type=int, default=0,
                   help="Override check interval in hours (default: from env)")
    p.add_argument("--test-slack", action="store_true",
                   help="Send a test message to Slack and exit")
    p.add_argument("--status", action="store_true",
                   help="Show state store statistics and exit")
    p.add_argument("--log-level", default=None,
                   help="Override log level (DEBUG, INFO, WARNING, ERROR)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # ── logging ──────────────────────────────────────────────────────────────
    from config import (
        LOG_LEVEL, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID,
        TWITTER_BEARER_TOKEN, LINKEDIN_ACCESS_TOKEN,
        CHECK_INTERVAL_HOURS, STATE_DB_PATH,
    )
    level = (args.log_level or LOG_LEVEL).upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    _banner()

    # ── --test-slack ─────────────────────────────────────────────────────────
    if args.test_slack:
        _test_slack(SLACK_BOT_TOKEN, SLACK_CHANNEL_ID)
        return

    # ── --status ─────────────────────────────────────────────────────────────
    if args.status:
        _show_status(STATE_DB_PATH)
        return

    # ── interval override ────────────────────────────────────────────────────
    interval = args.interval if args.interval > 0 else CHECK_INTERVAL_HOURS

    # ── build monitor ────────────────────────────────────────────────────────
    monitor = Monitor(
        state_db=STATE_DB_PATH,
        slack_token=SLACK_BOT_TOKEN,
        slack_channel=SLACK_CHANNEL_ID,
        twitter_token=TWITTER_BEARER_TOKEN,
        linkedin_token=LINKEDIN_ACCESS_TOKEN,
        interval_hours=interval,
    )

    # ── graceful shutdown ────────────────────────────────────────────────────
    def _handle_signal(signum, _frame):
        signame = signal.Signals(signum).name
        log.info("Received %s — shutting down …", signame)
        monitor.shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── run ──────────────────────────────────────────────────────────────────
    if args.once:
        monitor.run_cycle()
    else:
        monitor.run_forever()


if __name__ == "__main__":
    main()
