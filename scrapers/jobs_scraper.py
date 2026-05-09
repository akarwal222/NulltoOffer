import os
import re
import time
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
import requests

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
# Future upgrade: curious_coder/linkedin-jobs-search-scraper — also returns recruiter and company contact details, useful for Phase 2 networking feature
ACTOR_ID = "curious_coder/linkedin-jobs-scraper"
COUNT_PER_URL = 30

_CITY_LOCATION_MAP = [
    ("New York", "New York, United States"),
    ("San Francisco", "San Francisco, United States"),
    ("Manchester", "Manchester, United Kingdom"),
    ("Edinburgh", "Edinburgh, United Kingdom"),
    ("Delhi", "Delhi, India"),
    ("Mumbai", "Mumbai, India"),
    ("Bangalore", "Bangalore, India"),
    ("Hyderabad", "Hyderabad, India"),
    ("Remote", "Worldwide"),
    ("London", "London, United Kingdom"),
]


def _is_recent(posted_at: str, max_days: int = 60) -> bool:
    if not posted_at:
        return True
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - dt <= timedelta(days=max_days)
    except Exception:
        return True


def _split_term(term: str) -> tuple[str, str]:
    for city, location in _CITY_LOCATION_MAP:
        if city.lower() in term.lower():
            keywords = re.sub(r'\b' + re.escape(city) + r'\b', '', term, flags=re.IGNORECASE)
            keywords = re.sub(r'\s+', ' ', keywords).strip()
            return keywords, location
    return term, "London, United Kingdom"


def _actor_url(actor_id: str) -> str:
    return f"https://api.apify.com/v2/acts/{actor_id.replace('/', '~')}/runs"


def _build_search_urls(search_terms: list[str]) -> list[str]:
    base = "https://www.linkedin.com/jobs/search/"
    urls = []
    for term in search_terms:
        keywords, location = _split_term(term)
        urls.append(base + "?" + urllib.parse.urlencode({
            "keywords": keywords,
            "location": location,
            "position": 1,
            "pageNum": 0,
        }))
    return urls


def _start_actor_run(token: str, search_terms: list[str]) -> str:
    url = _actor_url(ACTOR_ID)
    payload = {
        "urls": _build_search_urls(search_terms),
        "count": COUNT_PER_URL,
        "scrapeCompany": True,
        "splitByLocation": False,
    }
    resp = requests.post(url, params={"token": token}, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def _wait_for_run(token: str, run_id: str, timeout: int = 180) -> None:
    url = f"{APIFY_BASE}/actor-runs/{run_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(url, params={"token": token}, timeout=15)
        resp.raise_for_status()
        status = resp.json()["data"]["status"]
        if status == "SUCCEEDED":
            return
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")
        time.sleep(5)
    raise TimeoutError(f"Apify run {run_id} did not finish within {timeout}s")


def _fetch_results(token: str, run_id: str) -> list[dict]:
    url = f"{APIFY_BASE}/actor-runs/{run_id}/dataset/items"
    resp = requests.get(url, params={"token": token, "clean": "true", "format": "json", "limit": 200}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extract_salary(item: dict) -> str:
    insights = item.get("salaryInsights") or {}
    breakdown = insights.get("compensationBreakdown") or []
    if breakdown and isinstance(breakdown, list):
        first = breakdown[0]
        low = first.get("minSalary")
        high = first.get("maxSalary")
        if low and high:
            return f"£{int(float(low)):,}–£{int(float(high)):,}"
        if low:
            return f"£{int(float(low)):,}+"

    salary_info = item.get("salaryInfo") or []
    if salary_info and isinstance(salary_info, list):
        return "–".join(str(s) for s in salary_info if s)

    return ""


def _normalise(item: dict) -> dict:
    workplace = item.get("workplaceTypes") or []
    if isinstance(workplace, list):
        workplace = ", ".join(workplace)

    return {
        "title": item.get("title") or "",
        "company": item.get("companyName") or "",
        "location": item.get("location") or "",
        "salary": _extract_salary(item),
        "description": item.get("descriptionText") or "",
        "url": item.get("link") or "",
        "source": "LinkedIn Jobs",
        "type": "listing",
        "poster_name": item.get("jobPosterName") or "",
        "poster_title": item.get("jobPosterTitle") or "",
        "poster_profile_url": item.get("jobPosterProfileUrl") or "",
        "poster_photo": item.get("jobPosterPhoto") or "",
        "company_linkedin_url": item.get("companyLinkedinUrl") or "",
        "company_logo": item.get("companyLogo") or "",
        "company_website": item.get("companyWebsite") or "",
        "company_description": item.get("companyDescription") or "",
        "company_employees_count": item.get("companyEmployeesCount") or 0,
        "company_slogan": item.get("companySlogan") or "",
        "company_address": item.get("companyAddress") or {},
        "apply_url": item.get("applyUrl") or "",
        "seniority_level": item.get("seniorityLevel") or "",
        "employment_type": item.get("employmentType") or "",
        "workplace_type": workplace,
        "posted_at": item.get("postedAt") or "",
        "applicants_count": str(item.get("applicantsCount") or ""),
    }


def scrape_jobs(search_terms: list[str]) -> list[dict]:
    token = os.environ["APIFY_API_TOKEN"]
    logger.info("Starting LinkedIn jobs scraper (actor: %s)", ACTOR_ID)

    run_id = _start_actor_run(token, search_terms)
    logger.info("Actor run started: %s — waiting for completion...", run_id)

    _wait_for_run(token, run_id)
    raw = _fetch_results(token, run_id)

    jobs = [_normalise(item) for item in raw if item.get("link")]
    original_count = len(jobs)
    jobs = [j for j in jobs if _is_recent(j.get("posted_at", ""))]
    logger.info("Jobs after age filter: %d (removed %d stale)", len(jobs), original_count - len(jobs))
    logger.info("Jobs scraper: %d listings retrieved", len(jobs))
    return jobs
