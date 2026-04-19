import json
import os
import re
import time
import logging
import urllib.parse
import anthropic
import requests

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "supreme_coder/linkedin-post"
MODEL = "claude-haiku-4-5-20251001"


def _actor_url(actor_id: str) -> str:
    return f"https://api.apify.com/v2/acts/{actor_id.replace('/', '~')}/runs"
SEARCH_QUERIES = [
    "hiring ML engineer London",
    "hiring AI engineer London",
    "looking for NLP engineer",
    "we're hiring machine learning London",
    "data scientist London hiring",
]
MAX_RESULTS = 20

_NON_UK_LOCATIONS = [
    "egypt", "uae", "dubai", "india", "pakistan", "nigeria",
    "canada", "australia", "united states", "usa", "singapore",
    "germany", "france", "netherlands", "spain", "italy",
]

_LONDON_AREAS = [
    "farringdon", "shoreditch", "canary wharf", "king's cross", "liverpool street",
    "soho", "clerkenwell", "moorgate", "old street", "bermondsey", "southwark",
    "victoria", "waterloo", "london bridge", "paddington", "hammersmith", "holborn",
    "ec1", "ec2", "ec3", "ec4", "wc1", "wc2", "e1", "n1", "se1", "sw1",
]

_CONFIDENTIAL_PHRASES = [
    "one of our clients", "our client", "a client of ours",
    "confidential client", "stealth startup",
]

# Exact-prefix patterns checked first — highest precision
_TITLE_EXACT_PREFIXES = [
    r"We'?re Hiring:\s*(.+)",
    r"🚀\s*Hiring:\s*(.+)",
    r"HIRING:\s*(.+)",
    r"Now Hiring:\s*(.+)",
    r"Currently Hiring:\s*(.+)",
    r"Role:\s*(.+)",
    r"Position:\s*(.+)",
    r"Job Title:\s*(.+)",
    r"Opening:\s*(.+)",
]

# Broader patterns — used after exact prefixes fail
_TITLE_PATTERNS = [
    r"[Ww]e'?re hiring (?:a |an )?([A-Za-z][A-Za-z0-9\s\-/]{2,50})",
    r'[Ll]ooking for (?:a |an )?([A-Za-z][A-Za-z0-9\s\-/]{2,50})',
    r'[Hh]iring (?:a |an )?([A-Za-z][A-Za-z0-9\s\-/]{2,50})',
    r'[Jj]oin (?:us )?as (?:a |an )?([A-Za-z][A-Za-z0-9\s\-/]{2,50})',
]

_TITLE_KEYWORDS = [
    "ML Engineer", "AI Engineer", "NLP Engineer", "Machine Learning Engineer",
    "LLM Engineer", "Data Scientist",
]

_CAPS_NOISE = {"WE", "ARE", "NOW", "THE", "FOR", "AND", "OUR", "YOU", "THIS", "THAT", "WITH", "HIRING"}

# First words that indicate a sentence fragment, not a role title
_FRAGMENT_FIRST_WORDS = {"who", "enjoy", "working", "close", "to", "the", "and", "for", "with", "in", "at"}


def _validate_title(candidate: str) -> str | None:
    """Return cleaned title if valid, None if it looks like a fragment."""
    cleaned = candidate.strip().rstrip(",.!?:")
    if not (3 <= len(cleaned) <= 60):
        return None
    if cleaned[0].islower():
        return None
    first_word = cleaned.split()[0].lower().rstrip(",.!?:")
    if first_word in _FRAGMENT_FIRST_WORDS:
        return None
    return cleaned


def _infer_title(post_text: str) -> str:
    # 1. Exact high-precision prefixes scanned across full post text
    for pattern in _TITLE_EXACT_PREFIXES:
        match = re.search(pattern, post_text, re.MULTILINE)
        if match:
            words = match.group(1).strip().split()[:6]
            candidate = " ".join(words)
            result = _validate_title(candidate)
            if result:
                return result

    # 2. Broader hiring phrases
    for pattern in _TITLE_PATTERNS:
        match = re.search(pattern, post_text)
        if match:
            words = match.group(1).strip().split()[:6]
            candidate = " ".join(words)
            result = _validate_title(candidate)
            if result:
                return result

    # 3. ALL CAPS role title in first 100 chars — require 2+ meaningful words
    caps_match = re.search(r'\b([A-Z]{2,}(?:\s+[A-Z]{2,}){1,5})\b', post_text[:100])
    if caps_match:
        tokens = caps_match.group(1).strip().split()
        meaningful = [t for t in tokens if t not in _CAPS_NOISE]
        if len(meaningful) >= 2:
            result = _validate_title(caps_match.group(1).title())
            if result:
                return result

    # 4. Keyword fallback
    for keyword in _TITLE_KEYWORDS:
        if keyword.lower() in post_text.lower():
            return keyword

    return "ML/AI Role"


def _build_search_urls() -> list[str]:
    base = "https://www.linkedin.com/search/results/content/"
    return [f"{base}?keywords={urllib.parse.quote(q)}" for q in SEARCH_QUERIES]


def _start_actor_run(token: str) -> str:
    url = _actor_url(ACTOR_ID)
    payload = {
        "urls": _build_search_urls(),
        "limitPerSource": MAX_RESULTS,
        "deepScrape": True,
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


def _extract_company(item: dict, author: dict, poster_title: str, post_text: str, poster_name: str) -> str:
    confidential_label = f"Confidential Client (via {poster_name})" if poster_name else "Confidential Client"

    if any(phrase in post_text.lower() for phrase in _CONFIDENTIAL_PHRASES):
        return confidential_label

    direct = (
        item.get("companyName")
        or item.get("authorCompanyName")
        or item.get("organizationName")
        or author.get("companyName")
        or author.get("company")
        or author.get("organizationName")
    )
    if direct:
        return direct

    for separator in (" at ", " @ ", " - "):
        if separator in poster_title:
            return poster_title.split(separator, 1)[1].strip()

    snippet = post_text[:300]
    for pattern in [
        r"([A-Z][A-Za-z0-9& ]{1,40})\s+is hiring",
        r"([A-Z][A-Za-z0-9& ]{1,40})\s+is looking for",
        r"joining\s+([A-Z][A-Za-z0-9& ]{1,40})",
    ]:
        match = re.search(pattern, snippet)
        if match:
            return match.group(1).strip()

    # Fell back to poster name — treat as recruiter hiding client
    return confidential_label


def _extract_location(post_text: str) -> str:
    lower = post_text.lower()

    non_uk = next((loc for loc in _NON_UK_LOCATIONS if loc in lower), None)
    is_remote = "remote" in lower
    is_uk = (
        "london" in lower
        or " uk " in lower
        or "united kingdom" in lower
        or any(area in lower for area in _LONDON_AREAS)
    )

    if non_uk and not is_remote and not is_uk:
        return non_uk.title()
    if is_remote:
        return "Remote"
    if is_uk:
        return "London, UK"
    return "Location not specified"


def _extract_salary(post_text: str) -> str:
    salary_patterns = [
        r'[£$][\d,]+[Kk]?\s*[-–—]\s*[£$][\d,]+[Kk]?',
        r'up\s+to\s+[£$][\d,]+[Kk]?',
        r'(?:salary|OTE|base)[:\s]+[£$][\d,]+[Kk]?',
        r'[£$][\d,]+[Kk]',
        r'\$[\d,]+[Kk]',
    ]

    salary = ""
    for pattern in salary_patterns:
        match = re.search(pattern, post_text, re.IGNORECASE)
        if match:
            salary = match.group(0).strip()
            break

    equity_match = re.search(
        r'\+\s*(?:strong\s+)?equity(?:\s+included)?',
        post_text, re.IGNORECASE
    )
    if equity_match:
        equity_str = equity_match.group(0).strip()
        return f"{salary} {equity_str}".strip() if salary else equity_str

    return salary


def _extract_post_fields(post_text: str, poster_name: str, poster_title: str) -> dict:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = (
        "Extract structured information from this LinkedIn job post. "
        "Return ONLY a valid JSON object with these exact keys:\n"
        '{\n'
        '  "title": "job title being advertised, or null if not a job post",\n'
        '  "company": "hiring company name, or \'Confidential Client\' if the poster is a recruiter not naming their client",\n'
        '  "location": "job location — city/country, \'Remote\', or \'Location not specified\'",\n'
        '  "salary": "salary range as string e.g. \'£70K–£100K + equity\', or empty string if not mentioned",\n'
        '  "is_job_post": true or false\n'
        "}\n"
        "Do not include any explanation, markdown, or text outside the JSON object.\n\n"
        f"Poster name: {poster_name}\n"
        f"Poster title: {poster_title}\n"
        f"Post text:\n{post_text}"
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def _normalise(item: dict) -> dict | None:
    post_text = item.get("text") or item.get("content") or item.get("postText") or ""
    author = item.get("author") or item.get("authorProfile") or {}
    if isinstance(author, str):
        author = {}

    poster_name = author.get("name") or author.get("fullName") or item.get("authorName") or ""
    poster_title = author.get("headline") or author.get("title") or item.get("authorHeadline") or ""
    url = item.get("url") or item.get("postUrl") or ""

    try:
        fields = _extract_post_fields(post_text, poster_name, poster_title)
        if not fields.get("is_job_post"):
            return None
        company = fields.get("company") or "Confidential Client"
        if company == "Confidential Client" and poster_name:
            company = f"Confidential Client (via {poster_name})"
        return {
            "title": fields.get("title") or "ML/AI Role",
            "company": company,
            "location": fields.get("location") or "Location not specified",
            "salary": fields.get("salary") or "",
            "poster_name": poster_name,
            "poster_title": poster_title,
            "post_text": post_text,
            "url": url,
            "source": "LinkedIn Post",
            "type": "informal_post",
        }
    except Exception as exc:
        logger.warning("Claude extraction failed, falling back to regex: %s", exc)

    return {
        "title": _infer_title(post_text),
        "company": _extract_company(item, author, poster_title, post_text, poster_name),
        "location": _extract_location(post_text),
        "salary": _extract_salary(post_text),
        "poster_name": poster_name,
        "poster_title": poster_title,
        "post_text": post_text,
        "url": url,
        "source": "LinkedIn Post",
        "type": "informal_post",
    }


def scrape_linkedin_posts() -> list[dict]:
    token = os.environ["APIFY_API_TOKEN"]
    logger.info("Starting LinkedIn posts scraper (actor: %s)", ACTOR_ID)

    try:
        run_id = _start_actor_run(token)
        logger.info("Actor run started: %s — waiting for completion...", run_id)

        _wait_for_run(token, run_id)
        raw = _fetch_results(token, run_id)

        posts = []
        for item in raw:
            if not (item.get("url") or item.get("postUrl")):
                continue
            normalised = _normalise(item)
            if normalised is not None:
                posts.append(normalised)
        logger.info("Posts scraper: %d posts retrieved", len(posts))
        return posts

    except Exception as exc:
        logger.warning("LinkedIn posts scraper failed (non-fatal): %s", exc)
        return []
