"""
LinkedIn Monitor — detects YC founder launch posts.
Uses RSS/Atom feeds and public post scraping (no API needed).
"""
import re
import logging
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("yc-monitor")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
}

YC_KEYWORDS_LINKEDIN = [
    "yc s26", "yc w26", "yc speedrun", "speedrun batch",
    "y combinator", "backed by yc", "got into yc",
    "accepted to yc", "yc founder", "yc company",
]


@dataclass
class LinkedInPost:
    post_id: str
    author_name: str
    author_url: str
    text: str
    posted_at: str
    url: str = ""
    source: str = "linkedin"

    def to_dict(self) -> dict:
        return {
            "post_id": self.post_id,
            "author_name": self.author_name,
            "author_url": self.author_url,
            "text": self.text,
            "posted_at": self.posted_at,
            "url": self.url,
            "source": self.source,
        }


def scrape_linkedin_hashtag(hashtag: str = "ycombinator",
                            max_posts: int = 20) -> list[LinkedInPost]:
    """Scrape LinkedIn hashtag feed for YC-related posts."""
    posts = []
    url = f"https://www.linkedin.com/hashtag/{hashtag}/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
        if resp.status_code != 200:
            log.warning("LinkedIn hashtag scrape returned %d", resp.status_code)
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        # LinkedIn public pages are heavily JS-rendered; look for any
        # embedded JSON-LD or script data
        scripts = soup.find_all("script", type="application/ld+json")
        for s in scripts:
            import json
            try:
                data = json.loads(s.string or "{}")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get("@type") in ("PostingAction", "Article"):
                        author = item.get("author", {})
                        posts.append(LinkedInPost(
                            post_id=item.get("@id", ""),
                            author_name=author.get("name", ""),
                            author_url=author.get("url", ""),
                            text=item.get("articleBody",
                                         item.get("description", "")),
                            posted_at=item.get("datePublished", ""),
                            url=item.get("url", ""),
                        ))
            except (json.JSONDecodeError, TypeError):
                continue

        log.info("LinkedIn hashtag #%s: found %d posts", hashtag, len(posts))
    except Exception as e:
        log.error("LinkedIn scrape failed: %s", e)
    return posts


def detect_yc_founders_linkedin(posts: list[LinkedInPost],
                                confirmed_slugs: set[str]) -> list[LinkedInPost]:
    """Filter LinkedIn posts to YC founder announcements."""
    matches = []
    for post in posts:
        text_lower = post.text.lower()
        if any(kw in text_lower for kw in YC_KEYWORDS_LINKEDIN):
            matches.append(post)
    log.info("LinkedIn YC founder detection: %d matches from %d posts",
             len(matches), len(posts))
    return matches
