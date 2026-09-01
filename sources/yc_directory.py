"""
YC Directory Scraper — detects new YC/Speedrun companies.
Uses requests + HTML parsing with Inertia.js data-page extraction.
"""
import json
import re
import logging
import requests
from typing import Optional

log = logging.getLogger("yc-monitor")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}


class YCCompany:
    def __init__(self, name: str, slug: str, batch: str = "",
                 description: str = "", url: str = ""):
        self.name = name
        self.slug = slug
        self.batch = batch
        self.description = description
        self.url = url or f"https://www.ycombinator.com/companies/{slug}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "batch": self.batch,
            "description": self.description,
            "url": self.url,
        }


def _parse_from_html(html: str) -> list[YCCompany]:
    """Extract companies from YC HTML — links with /companies/<slug>."""
    companies = []
    seen = set()
    for match in re.finditer(r'href="(/companies/([^/?"]+))"', html):
        slug = match.group(2)
        if slug in seen or len(slug) < 2:
            continue
        seen.add(slug)
        companies.append(YCCompany(
            name=slug.replace("-", " ").title(),
            slug=slug,
        ))
    return companies


def _parse_from_data_page(html: str) -> list[YCCompany]:
    """Extract from Inertia.js data-page attribute (server-rendered props)."""
    companies = []
    match = re.search(r'data-page="([^"]+)"', html)
    if not match:
        return companies
    try:
        import html as html_mod
        raw = html_mod.unescape(match.group(1))
        data = json.loads(raw)
        props = data.get("props", {})
        # Companies might be nested in various places
        for key in ("companies", "results", "data"):
            items = props.get(key, [])
            if isinstance(items, list) and items:
                for item in items:
                    if isinstance(item, dict):
                        slug = item.get("slug", "")
                        if slug:
                            companies.append(YCCompany(
                                name=item.get("name", slug),
                                slug=slug,
                                batch=item.get("batch", ""),
                                description=item.get("one_liner",
                                                    item.get("description", "")),
                            ))
                break
    except (json.JSONDecodeError, TypeError):
        pass
    return companies


def scrape_yc_directory(url: str = "https://www.ycombinator.com/companies",
                        batch_filter: str = "") -> list[YCCompany]:
    """Scrape YC company directory. Returns list of YCCompany."""
    companies = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Try Inertia data-page first
        companies = _parse_from_data_page(html)
        if companies:
            log.info("Parsed %d companies from data-page", len(companies))
        else:
            # Fallback: extract from HTML links
            companies = _parse_from_html(html)
            log.info("Extracted %d companies from HTML links", len(companies))

        # Apply batch filter if specified
        if batch_filter:
            companies = [c for c in companies
                         if batch_filter.lower() in c.batch.lower()
                         or batch_filter.lower() in c.name.lower()]

    except Exception as e:
        log.error("YC directory scrape failed: %s", e)
    return companies


def check_new_yc_companies(seen_slugs: set[str],
                           batch_filter: str = "") -> list[YCCompany]:
    """Compare live directory against known slugs. Returns only new ones."""
    all_companies = scrape_yc_directory(batch_filter=batch_filter)
    new = [c for c in all_companies if c.slug not in seen_slugs]
    log.info("YC check: %d total, %d new (filter=%r)",
             len(all_companies), len(new), batch_filter)
    return new
