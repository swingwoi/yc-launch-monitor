"""
a16z Speedrun source — public REST API, no key required.
Endpoints:
    https://speedrun-api.a16z.com/api/companies/companies/
    (paginated; each record includes founders, X/LinkedIn/website URLs, cohort)
"""
import logging
import requests

log = logging.getLogger("yc-monitor")

API_BASE = "https://speedrun-api.a16z.com/api/companies/companies/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"}
PAGE_SIZE = 100


class SpeedrunCompany:
    def __init__(self, name: str, slug: str, cohort: str = "",
                 description: str = "", key_signal: str = "",
                 industries=None, website_url: str = "",
                 x_url: str = "", linkedin_url: str = "",
                 country: str = "", region: str = "", city: str = "",
                 founders=None, url: str = ""):
        self.name = name
        self.slug = slug
        self.cohort = cohort
        self.description = description
        self.key_signal = key_signal
        self.industries = industries or []
        self.website_url = website_url
        self.x_url = x_url
        self.linkedin_url = linkedin_url
        self.country = country
        self.region = region
        self.city = city
        self.founders = founders or []
        self.url = url or f"https://speedrun.a16z.com/company/{slug}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "slug": self.slug,
            "cohort": self.cohort,
            "description": self.description,
            "key_signal": self.key_signal,
            "industries": self.industries,
            "website_url": self.website_url,
            "x_url": self.x_url,
            "linkedin_url": self.linkedin_url,
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "founders": self.founders,
            "url": self.url,
        }


def _parse_founders(founder_set) -> list:
    out = []
    if not founder_set:
        return out
    for f in founder_set:
        if isinstance(f, dict):
            out.append({
                "name": f.get("name", ""),
                "x_url": f.get("x_url", ""),
                "linkedin_url": f.get("linkedin_url", ""),
                "role": f.get("role", ""),
            })
        elif isinstance(f, str):
            out.append({"name": f})
    return out


def _parse_company(rec: dict) -> SpeedrunCompany:
    return SpeedrunCompany(
        name=rec.get("name", rec.get("slug", "")),
        slug=rec.get("slug", ""),
        cohort=rec.get("cohort", ""),
        description=rec.get("description", ""),
        key_signal=rec.get("key_signal", ""),
        industries=rec.get("industries", []) or [],
        website_url=rec.get("website_url", ""),
        x_url=rec.get("x_url", ""),
        linkedin_url=rec.get("linkedin_url", ""),
        country=rec.get("country", ""),
        region=rec.get("region", ""),
        city=rec.get("city", ""),
        founders=_parse_founders(rec.get("founder_set")),
    )


def fetch_all_companies() -> list[SpeedrunCompany]:
    """Fetch every Speedrun company via pagination."""
    companies = []
    url = f"{API_BASE}?limit={PAGE_SIZE}"
    page = 0
    while url:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for rec in data.get("results", []):
            companies.append(_parse_company(rec))
        url = data.get("next")
        page += 1
        if page > 60:  # safety cap
            break
    log.info("Speedrun: fetched %d companies", len(companies))
    return companies


def get_new_speedrun_companies(seen_slugs: set[str]) -> list[SpeedrunCompany]:
    """Return Speedrun companies not already seen."""
    all_companies = fetch_all_companies()
    new = [c for c in all_companies if c.slug not in seen_slugs]
    if all_companies:
        # Store all known slugs upstream so next run only diffs new ones
        log.info("Speedrun new: %d / %d", len(new), len(all_companies))
    return new