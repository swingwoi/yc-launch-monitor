"""
X/Twitter Monitor — detects YC founder launch posts.
Uses Twitter API v2 (recent search endpoint).
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

log = logging.getLogger("yc-monitor")

# Keywords that signal YC founder announcements
YC_KEYWORDS = [
    "YC S26", "YC W26", "YC F26", "YC S25", "YC W25",
    "yc batch", "speedrun batch", "yc speedrun",
    "backed by y combinator", "y combinator",
    "got into yc", "joined yc", "accepted to yc",
    "yc founder", "yc company",
]

SEARCH_QUERY = (
    "(" + " OR ".join(f'"{kw}"' for kw in YC_KEYWORDS[:8]) + ")"
    " -is:retweet lang:en"
)


@dataclass
class TwitterPost:
    tweet_id: str
    author_name: str
    author_handle: str
    text: str
    created_at: str
    url: str = ""
    source: str = "x"

    def to_dict(self) -> dict:
        return {
            "tweet_id": self.tweet_id,
            "author_name": self.author_name,
            "author_handle": self.author_handle,
            "text": self.text,
            "created_at": self.created_at,
            "url": self.url,
            "source": self.source,
        }


def search_yc_posts(bearer_token: str,
                    max_results: int = 20,
                    since_id: Optional[str] = None) -> list[TwitterPost]:
    """Search recent tweets for YC founder announcements."""
    if not bearer_token:
        log.warning("No Twitter bearer token — skipping X monitor")
        return []

    url = "https://api.twitter.com/2/tweets/search/recent"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    params = {
        "query": SEARCH_QUERY,
        "max_results": min(max_results, 100),
        "tweet.fields": "created_at,author_id,text",
        "expansions": "author_id",
        "user.fields": "name,username",
    }
    if since_id:
        params["since_id"] = since_id

    posts = []
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60))
            log.warning("Twitter rate limited — retry after %ds", retry_after)
            return []
        resp.raise_for_status()
        data = resp.json()

        # Build author lookup
        users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}

        for tweet in data.get("data", []):
            author = users.get(tweet.get("author_id", ""), {})
            handle = author.get("username", "unknown")
            name = author.get("name", handle)
            tid = tweet["id"]
            posts.append(TwitterPost(
                tweet_id=tid,
                author_name=name,
                author_handle=handle,
                text=tweet.get("text", ""),
                created_at=tweet.get("created_at", ""),
                url=f"https://x.com/{handle}/status/{tid}",
            ))
        log.info("X monitor: found %d YC-related posts", len(posts))

    except requests.RequestException as e:
        log.error("Twitter search failed: %s", e)

    return posts


def detect_early_founders(posts: list[TwitterPost],
                          confirmed_slugs: set[str]) -> list[TwitterPost]:
    """Filter posts to those from founders not yet in YC directory."""
    early = []
    for post in posts:
        text_lower = post.text.lower()
        # If post mentions a specific company not in confirmed list,
        # it's likely an early founder announcement
        if any(kw.lower() in text_lower for kw in YC_KEYWORDS):
            early.append(post)
    log.info("Early founder detection: %d candidates from %d posts",
             len(early), len(posts))
    return early
