import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"
ACTOR_ID = "misceres/indeed-scraper"
SEARCH_TERMS = [
    "ML Engineer London",
    "AI Engineer London",
    "NLP Engineer London",
    "Applied AI Engineer London",
    "Machine Learning Engineer London",
    "LLM Engineer London",
]
MAX_RESULTS = 50


def _start_actor_run(token: str) -> str:
    url = f"{APIFY_BASE}/acts/{ACTOR_ID}/runs"
    payload = {
        "queries": [
            {"keyword": term, "location": "London, United Kingdom"}
            for term in SEARCH_TERMS
        ],
        "maxItems": MAX_RESULTS,
        "country": "GB",
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
    return {
        "title": item.get("title") or item.get("jobTitle") or "",
        "company": item.get("company") or item.get("companyName") or "",
        "location": item.get("location") or item.get("jobLocation") or "",
        "salary": item.get("salary") or item.get("salaryText") or "",
        "description": item.get("description") or item.get("jobDescription") or "",
        "url": item.get("url") or item.get("jobUrl") or "",
        "source": "Indeed",
        "type": "listing",
    }


def scrape_indeed_jobs() -> list[dict]:
    token = os.environ["APIFY_API_TOKEN"]
    logger.info("Starting Indeed scraper (actor: %s)", ACTOR_ID)

    try:
        run_id = _start_actor_run(token)
        logger.info("Actor run started: %s — waiting for completion...", run_id)

        dataset_id = _wait_for_run(token, run_id)
        raw = _fetch_dataset(token, dataset_id)

        jobs = [_normalise(item) for item in raw if item.get("url") or item.get("jobUrl")]
        logger.info("Indeed scraper: %d listings retrieved", len(jobs))
        return jobs

    except Exception as exc:
        logger.warning("Indeed scraper failed (non-fatal): %s", exc)
        return []
