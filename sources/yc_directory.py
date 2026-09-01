"""
YC Directory source — uses the public `yc-oss` GitHub Pages mirror
of YC's Algolia index. It exposes an incremental `changes/latest.json`
feed with an `added` array = newly listed companies. No API key, updates
daily, works from pure Python.

Endpoint (public, no key):
    https://yc-oss.github.io/api/changes/latest.json
"""
import json
import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger("yc-monitor")

BASE = "https://yc-oss.github.io/api"
CHANGES_URL = f"{BASE}/changes/latest.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


class YCCompany:
    """A YC directory company / new-listing record."""

    def __init__(self, name: str, slug: str, batch: str = "",
                 one_liner: str = "", website: str = "",
                 industry: str = "", launched_at: str = "",
                 url: str = "", **extra):
        self.name = name
        self.slug = slug
        self.batch = batch
        self.one_liner = one_liner
        self.website = website
        self.industry = industry
        self.launched_at = launched_at
        self.url = url or f"https://www.ycombinator.com/companies/{slug}"
        self.extra = extra

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "slug": self.slug,
            "batch": self.batch,
            "one_liner": self.one_liner,
            "website": self.website,
            "industry": self.industry,
            "launched_at": self.launched_at,
            "url": self.url,
        }
        d.update(self.extra)
        return d


def _parse_company(rec: dict) -> YCCompany:
    return YCCompany(
        name=rec.get("name", rec.get("slug", "")),
        slug=rec.get("slug", ""),
        batch=rec.get("batch", ""),
        one_liner=rec.get("one_liner", ""),
        website=rec.get("website", ""),
        industry=rec.get("industry", ""),
        launched_at=rec.get("launched_at", ""),
        url=rec.get("url") or f"https://www.ycombinator.com/companies/{rec.get('slug','')}",
        long_description=rec.get("long_description", "") or "",
        team_size=rec.get("team_size", "") or "",
        stage=rec.get("stage", "") or "",
        regions=rec.get("regions", []) or [],
        status=rec.get("status", "") or "",
    )


def fetch_changes() -> dict:
    """Fetch the incremental changes feed. Raises on error."""
    resp = requests.get(CHANGES_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_new_yc_companies(seen_slugs: set[str]) -> list[YCCompany]:
    """Return YC companies newly listed, minus those already seen.

    Reads the mirror's incremental `added` array and filters against the
    caller's seen set so nothing is alerted twice.
    """
    data = fetch_changes()
    added = data.get("added", [])
    log.info("YC changes feed: added=%d current_total=%s (generated %s)",
             len(added), data.get("summary", {}).get("current_total"),
             data.get("generated_at"))
    new = []
    for rec in added:
        slug = rec.get("slug", "")
        if slug and slug not in seen_slugs:
            new.append(_parse_company(rec))
    log.info("YC new (after dedup): %d", len(new))
    return new