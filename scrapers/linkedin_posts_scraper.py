import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "apify/linkedin-scraper"
SEARCH_QUERIES = [
    "hiring ML engineer London",
    "hiring AI engineer London",
    "looking for NLP engineer",
    "we're hiring machine learning London",
]
MAX_RESULTS = 20


def _infer_title(post_text: str) -> str:
    for keyword in ["ML Engineer", "AI Engineer", "NLP Engineer", "Machine Learning Engineer", "LLM Engineer"]:
        if keyword.lower() in post_text.lower():
            return keyword
    return "ML/AI Role"


def _start_actor_run(token: str) -> str:
    url = f"{APIFY_BASE}/acts/{ACTOR_ID}/runs"
    payload = {
        "queries": SEARCH_QUERIES,
        "maxResults": MAX_RESULTS,
        "type": "posts",
    }
    resp = requests.post(url, params={"token": token}, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def _wait_for_run(token: str, run_id: str, timeout: int = 300) -> str:
    url = f"{APIFY_BASE}/actor-runs/{run_id}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(url, params={"token": token}, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        status = data["status"]
        if status == "SUCCEEDED":
            return data["defaultDatasetId"]
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify run {run_id} ended with status: {status}")
        time.sleep(10)
    raise TimeoutError(f"Apify run {run_id} did not finish within {timeout}s")


def _fetch_dataset(token: str, dataset_id: str) -> list[dict]:
    url = f"{APIFY_BASE}/datasets/{dataset_id}/items"
    resp = requests.get(url, params={"token": token, "format": "json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _normalise(item: dict) -> dict:
    post_text = item.get("text") or item.get("content") or item.get("postText") or ""
    author = item.get("author") or item.get("authorProfile") or {}
    if isinstance(author, str):
        author = {}

    return {
        "title": _infer_title(post_text),
        "company": author.get("companyName") or author.get("company") or item.get("companyName") or "",
        "poster_name": author.get("name") or author.get("fullName") or item.get("authorName") or "",
        "poster_title": author.get("headline") or author.get("title") or item.get("authorHeadline") or "",
        "post_text": post_text,
        "url": item.get("url") or item.get("postUrl") or "",
        "source": "LinkedIn Post",
        "type": "informal_post",
    }


def scrape_linkedin_posts() -> list[dict]:
    token = os.environ["APIFY_API_TOKEN"]
    logger.info("Starting LinkedIn posts scraper (actor: %s)", ACTOR_ID)

    try:
        run_id = _start_actor_run(token)
        logger.info("Actor run started: %s — waiting for completion...", run_id)

        dataset_id = _wait_for_run(token, run_id)
        raw = _fetch_dataset(token, dataset_id)

        posts = [_normalise(item) for item in raw if item.get("url") or item.get("postUrl")]
        logger.info("Posts scraper: %d posts retrieved", len(posts))
        return posts

    except Exception as exc:
        logger.warning("LinkedIn posts scraper failed (non-fatal): %s", exc)
        return []
