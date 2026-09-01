"""
YC Launch Monitor — Main orchestrator.
Ties together directory/speedrun monitoring, social monitoring, state
tracking, scheduling, and Slack alerting into a single polling loop.
"""
import time
import logging
from datetime import datetime, timezone

import config
from state_store import JsonlStore, make_company_id
from scheduler import Scheduler
from sources.yc_directory import get_new_yc_companies
from sources.speedrun import get_new_speedrun_companies
from sources.twitter_monitor import search_yc_posts, detect_early_founders
from sources.linkedin_monitor import scrape_linkedin_hashtag, detect_yc_founders_linkedin
from slack_transport import send_alert

log = logging.getLogger("yc-monitor")

SCHEDULER_QUEUE = str(config.BASE_DIR / "scheduler_queue.jsonl")


def _setup_logging():
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Monitor:
    """Main monitoring loop — polls sources, deduplicates, alerts via Slack."""

    def __init__(self):
        _setup_logging()
        self.store = JsonlStore(config.STATE_DB_PATH)
        self.scheduler = Scheduler(SCHEDULER_QUEUE)
        log.info(
            "Monitor initialised — state=%s, interval=%dh",
            config.STATE_DB_PATH,
            config.CHECK_INTERVAL_HOURS,
        )

    # ── source checks ────────────────────────────────────────────────
    def _check_yc_directory(self, now: str) -> list[dict]:
        """Incremental YC new-listings from the yc-oss changes feed."""
        seen = {r["slug"] for r in self.store.all()
                if r.get("source") == "yc_directory" and "slug" in r}
        out = []
        for company in get_new_yc_companies(seen):
            cid = make_company_id("yc_directory", company.slug)
            if self.store.has("id", cid):
                continue
            rec = {
                "id": cid, "source": "yc_directory", "detected_at": now,
                "alert_type": "new_yc_company",
                "company_name": company.name, "batch": company.batch,
                "description": company.one_liner,
                "url": company.url, "website": company.website,
                "industry": company.industry, "team_size": company.extra.get("team_size", ""),
                "stage": company.extra.get("stage", ""),
                "slug": company.slug,
            }
            out.append(rec)
            self.store.append(rec)
        return out

    def _check_speedrun(self, now: str) -> list[dict]:
        """New a16z Speedrun companies from the public API.

        First run establishes a baseline: every existing slug is snapshotted
        but nothing is alerted, so we never spam 250+ alerts on day one.
        Subsequent runs alert only on newly-added slugs.
        """
        seen = {r["slug"] for r in self.store.all()
                if r.get("source") == "speedrun" and "slug" in r}
        first_run = not seen
        out = []
        for company in get_new_speedrun_companies(seen):
            cid = make_company_id("speedrun", company.slug)
            # On baseline (first) run, record the slug but alert nothing.
            if first_run:
                self.store.append({"id": cid, "source": "speedrun",
                                   "slug": company.slug, "detected_at": now,
                                   "name": company.name, "baseline": True})
                continue
            if self.store.has("id", cid):
                continue
            founders = company.founders or []
            fname = founders[0].get("name", "") if founders else ""
            rec = {
                "id": cid, "source": "speedrun", "detected_at": now,
                "alert_type": "new_speedrun_company",
                "company_name": company.name, "batch": company.cohort,
                "description": company.description or company.key_signal,
                "url": company.url,
                "website": company.website_url, "x_url": company.x_url,
                "linkedin_url": company.linkedin_url, "cohort": company.cohort,
                "founder_name": fname,
                "slug": company.slug,
            }
            out.append(rec)
            self.store.append(rec)
        return out

    def _check_twitter(self, now: str) -> list[dict]:
        posts = search_yc_posts(config.TWITTER_BEARER_TOKEN)
        early = detect_early_founders(posts, set())
        out = []
        for post in early:
            cid = make_company_id("x", post.tweet_id)
            if self.store.has("id", cid):
                continue
            rec = {
                "id": cid, "source": "x", "detected_at": now,
                "alert_type": "early_founder",
                "company_name": "Unknown company", "batch": "",
                "description": post.text[:400],
                "url": post.url,
                "founder_name": post.author_name, "founder_handle": post.author_handle,
                "original_text": post.text,
            }
            out.append(rec)
            self.store.append(rec)
        return out

    def _check_linkedin(self, now: str) -> list[dict]:
        posts = scrape_linkedin_hashtag("ycombinator")
        matches = detect_yc_founders_linkedin(posts, set())
        out = []
        for post in matches:
            cid = make_company_id("linkedin", post.post_id)
            if self.store.has("id", cid):
                continue
            rec = {
                "id": cid, "source": "linkedin", "detected_at": now,
                "alert_type": "early_founder",
                "company_name": "Unknown company", "batch": "",
                "description": post.text[:400],
                "url": post.url,
                "founder_name": post.author_name,
                "original_text": post.text,
            }
            out.append(rec)
            self.store.append(rec)
        return out

    # ── main cycle ──────────────────────────────────────────────────
    def run_cycle(self) -> list[dict]:
        """Execute one full monitoring pass across all sources."""
        log.info("=== Starting monitor cycle ===")
        now = _now()
        new_records: list[dict] = []

        for check in (self._check_yc_directory, self._check_speedrun,
                      self._check_twitter, self._check_linkedin):
            try:
                new_records.extend(check(now))
            except Exception as exc:
                log.error("%s check failed: %s", check.__name__, exc, exc_info=True)

        # Queue + push alerts (scheduler gives crash-safe retry)
        for rec in new_records:
            self.scheduler.enqueue({"type": "slack_alert", "record": rec})
        self._drain()

        log.info("Cycle complete: %d new detections queued", len(new_records))
        return new_records

    def _drain(self):
        def _send(record_dict: dict) -> str:
            rec = record_dict["record"]
            send_alert(rec)  # transport builds blocks + retries internally
            return f"sent:{rec['id']}"
        drained = self.scheduler.drain(_send, max_attempts=3)
        if drained:
            log.info("Scheduler drained %d alert(s)", len(drained))

    # ── run loop ────────────────────────────────────────────────────
    def run_forever(self, interval_hours=None):
        """Poll forever with a configurable interval (floor 60s)."""
        interval = interval_hours or config.CHECK_INTERVAL_HOURS
        interval_sec = max(interval * 3600, 60)
        log.info("Entering run_forever loop (interval=%.1fh)", interval)

        while True:
            try:
                self.run_cycle()
            except KeyboardInterrupt:
                log.info("Interrupted — shutting down")
                break
            except Exception as exc:
                log.error("Cycle raised unexpected error: %s", exc, exc_info=True)
            log.info("Sleeping %.0fs until next cycle…", interval_sec)
            try:
                time.sleep(interval_sec)
            except KeyboardInterrupt:
                log.info("Interrupted during sleep — shutting down")
                break


# ── CLI entrypoint ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="YC Launch Monitor")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--interval", type=float, default=None,
                        help="Override check interval in hours")
    args = parser.parse_args()

    monitor = Monitor()
    if args.once:
        monitor.run_cycle()
    else:
        monitor.run_forever(interval_hours=args.interval)