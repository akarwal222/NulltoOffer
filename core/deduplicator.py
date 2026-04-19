import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SEEN_JOBS_PATH = Path(__file__).parent.parent / "data" / "seen_jobs.json"


def _load_seen() -> dict:
    if not SEEN_JOBS_PATH.exists():
        return {}
    try:
        return json.loads(SEEN_JOBS_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("seen_jobs.json unreadable (%s) — starting fresh", exc)
        return {}


def _save_seen(seen: dict) -> None:
    SEEN_JOBS_PATH.write_text(json.dumps(seen, indent=2), encoding="utf-8")


def deduplicate(jobs: list[dict]) -> list[dict]:
    seen = _load_seen()
    new_jobs = [job for job in jobs if job.get("url") and job["url"] not in seen]
    logger.info("Deduplication: %d total → %d new (skipped %d)", len(jobs), len(new_jobs), len(jobs) - len(new_jobs))
    return new_jobs


def mark_seen(jobs: list[dict]) -> None:
    seen = _load_seen()
    added = 0
    for job in jobs:
        url = job.get("url")
        if url and url not in seen:
            seen[url] = True
            added += 1
    _save_seen(seen)
    logger.info("Marked %d new jobs as seen (total seen: %d)", added, len(seen))
