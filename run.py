#!/usr/bin/env python3
"""
YC Launch Monitor — entry point.

Detects new YC Directory / a16z Speedrun / X / LinkedIn signals and posts
enriched targeting alerts to a Slack channel.

Usage:
    python run.py                 # run forever (default interval)
    python run.py --once          # single check, then exit
    python run.py --interval 4    # override check interval to 4 hours
    python run.py --test-slack    # send a test message and exit
    python run.py --status        # show state store statistics
"""
import argparse
import logging
import signal
import sys

import config
from monitor import Monitor

log = logging.getLogger("yc-monitor")


def _banner() -> None:
    from datetime import datetime, timezone
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


def _test_slack() -> None:
    """Send a real test alert so the buyer can verify Slack delivery."""
    from slack_transport import send_alert
    from datetime import datetime, timezone

    resp = send_alert({
        "alert_type": "early_founder",
        "company_name": "YC Launch Monitor",
        "source": "yc_directory",
        "batch": "test",
        "description": "Slack integration test — if you can see this it works.",
        "url": "https://github.com/swingwoi/yc-launch-monitor",
        "website": "https://github.com/swingwoi/yc-launch-monitor",
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "founder_name": "—",
    })
    ok = resp.get("ok", resp.get("ts") is not None)
    print(f"[test-slack] {'Sent ok' if ok else 'Failed'}")
    if hasattr(resp.get("ok"), "__bool__") and not resp.get("ok", True):
        sys.exit(1)


def _show_status() -> None:
    from state_store import JsonlStore
    store = JsonlStore(config.STATE_DB_PATH)
    records = store.all()
    by_source: dict = {}
    for rec in records:
        src = rec.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
    print("═══════════════════════════════════════════")
    print("  YC Launch Monitor — State Store Stats")
    print("═══════════════════════════════════════════")
    print(f"  Total records tracked : {len(records)}")
    print(f"  State file            : {config.STATE_DB_PATH}")
    if by_source:
        print("  By source:")
        for src, cnt in sorted(by_source.items(), key=lambda x: -x[1]):
            print(f"    {src:20s} : {cnt}")
    print("═══════════════════════════════════════════")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="yc-launch-monitor",
        description="Monitor YC Directory / Speedrun / X / LinkedIn for new launches.",
    )
    p.add_argument("--once", action="store_true",
                   help="Run a single check cycle then exit")
    p.add_argument("--interval", type=float, default=None,
                   help="Override check interval in hours")
    p.add_argument("--test-slack", action="store_true",
                   help="Send a test message to Slack and exit")
    p.add_argument("--status", action="store_true",
                   help="Show state store statistics and exit")
    p.add_argument("--log-level", default=None,
                   help="Override log level (DEBUG, INFO, WARNING, ERROR)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    level = (args.log_level or config.LOG_LEVEL).upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _banner()

    if args.test_slack:
        _test_slack()
        return
    if args.status:
        _show_status()
        return

    monitor = Monitor()

    def _handle_signal(signum, _frame):
        log.info("Received signal %s — shutting down", signum)
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.once:
        monitor.run_cycle()
    else:
        monitor.run_forever(interval_hours=args.interval)


if __name__ == "__main__":
    main()