"""
YC Launch Monitor — Main orchestrator.
Ties together directory scraping, social monitoring, state tracking,
scheduling, and Slack alerting into a single polling loop.
"""
import time
import logging
from datetime import datetime, timezone

import config
from state_store import JsonlStore, make_company_id
from scheduler import Scheduler
from sources.yc_directory import check_new_yc_companies
from sources.twitter_monitor import search_yc_posts, detect_early_founders
from sources.linkedin_monitor import scrape_linkedin_hashtag, detect_yc_founders_linkedin
from slack_transport import send_alert, format_alert

log = logging.getLogger("yc-monitor")

SCHEDULER_QUEUE = str(config.BASE_DIR / "scheduler_queue.jsonl")


def _setup_logging():
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


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

    # ── helpers ──────────────────────────────────────────────────────
    def _known_slugs(self) -> set[str]:
        """Collect all previously-seen YC company slugs from state."""
        return {
            rec["slug"]
            for rec in self.store.all()
            if rec.get("source") == "yc_directory" and "slug" in rec
        }

    def _known_social_ids(self) -> set[str]:
        """Collect all previously-seen social post IDs from state."""
        return {
            rec.get("social_id", rec.get("tweet_id", rec.get("post_id", "")))
            for rec in self.store.all()
            if rec.get("source") in ("x", "linkedin") and rec.get("social_id")
        }

    def _alert_and_record(self, records: list[dict]):
        """Send Slack alert and persist each record."""
        for rec in records:
            payload = format_alert(rec)
            # Queue alert in the crash-safe scheduler
            self.scheduler.enqueue({
                "type": "slack_alert",
                "record_id": rec["id"],
                "payload": payload,
            })
            # Persist the detection record
            self.store.append(rec)
            log.info("Recorded %s (source=%s)", rec["id"], rec.get("source"))

    # ── main cycle ──────────────────────────────────────────────────
    def run_cycle(self):
        """Execute one full monitoring pass across all sources."""
        log.info("=== Starting monitor cycle ===")
        now = datetime.now(timezone.utc).isoformat()
        new_records: list[dict] = []

        # 1. YC Directory — new companies
        try:
            seen_slugs = self._known_slugs()
            new_companies = check_new_yc_companies(seen_slugs)
            for company in new_companies:
                cid = make_company_id("yc_directory", company.slug)
                if not self.store.has("id", cid):
                    rec = {
                        "id": cid,
                        "source": "yc_directory",
                        "detected_at": now,
                        **company.to_dict(),
                    }
                    new_records.append(rec)
            log.info("YC directory: %d new companies", len(new_companies))
        except Exception as exc:
            log.error("YC directory check failed: %s", exc, exc_info=True)

        # 2. X / Twitter — early founder posts
        try:
            twitter_posts = search_yc_posts(config.TWITTER_BEARER_TOKEN)
            known_social = self._known_social_ids()
            early = detect_early_founders(twitter_posts, self._known_slugs())
            for post in early:
                cid = make_company_id("x", post.tweet_id)
                if cid not in known_social and not self.store.has("id", cid):
                    rec = {
                        "id": cid,
                        "source": "x",
                        "social_id": post.tweet_id,
                        "detected_at": now,
                        **post.to_dict(),
                    }
                    new_records.append(rec)
            log.info("X monitor: %d early founder posts", len(early))
        except Exception as exc:
            log.error("X monitor failed: %s", exc, exc_info=True)

        # 3. LinkedIn — YC hashtag posts
        try:
            li_posts = scrape_linkedin_hashtag("ycombinator")
            known_social = self._known_social_ids()
            matches = detect_yc_founders_linkedin(li_posts, self._known_slugs())
            for post in matches:
                cid = make_company_id("linkedin", post.post_id)
                if cid not in known_social and not self.store.has("id", cid):
                    rec = {
                        "id": cid,
                        "source": "linkedin",
                        "social_id": post.post_id,
                        "detected_at": now,
                        **post.to_dict(),
                    }
                    new_records.append(rec)
            log.info("LinkedIn: %d YC founder matches", len(matches))
        except Exception as exc:
            log.error("LinkedIn monitor failed: %s", exc, exc_info=True)

        # 4. Persist + alert for all new detections
        if new_records:
            self._alert_and_record(new_records)
            log.info("Cycle complete: %d new detections queued for Slack", len(new_records))
        else:
            log.info("Cycle complete: no new detections")

        # 5. Drain any pending scheduler items (retry crashed alerts)
        def _send_slack_alert(payload: dict) -> str:
            send_alert(payload["payload"])
            return f"sent:{payload['record_id']}"

        drained = self.scheduler.drain(_send_slack_alert)
        if drained:
            log.info("Scheduler drained %d pending alerts", len(drained))

        return new_records

    # ── run loop ────────────────────────────────────────────────────
    def run_forever(self, interval_hours=None):
        """Poll forever with a configurable interval."""
        interval = interval_hours or config.CHECK_INTERVAL_HOURS
        interval_sec = max(interval * 3600, 60)  # floor of 60s
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
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Override check interval in hours",
    )
    args = parser.parse_args()

    monitor = Monitor()

    if args.once:
        monitor.run_cycle()
    else:
        monitor.run_forever(interval_hours=args.interval)
