import os
from datetime import datetime, timezone
from pymongo import MongoClient

_client = None
_db = None


def _get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(os.environ["MONGODB_URI"])
        _db = _client["nulltooffer"]
    return _db


def get_all_users() -> list[dict]:
    return list(_get_db()["users"].find({}, {"_id": 0}))


def job_exists(url: str) -> bool:
    return _get_db()["jobs"].find_one({"url": url}) is not None


def save_job(job: dict) -> None:
    fields = {
        k: job.get(k)
        for k in (
            "title", "company", "location", "salary", "description",
            "url", "source", "type", "poster_name", "poster_title", "scraped_at",
        )
    }
    _get_db()["jobs"].update_one({"url": job["url"]}, {"$set": fields}, upsert=True)


def save_user_job(user_job: dict) -> None:
    doc = {**user_job}
    doc.setdefault("status", "new")
    doc.setdefault("is_saved", False)
    doc.setdefault("created_at", datetime.now(timezone.utc))
    _get_db()["user_jobs"].update_one(
        {"clerk_user_id": doc["clerk_user_id"], "url": doc["url"]},
        {"$set": doc},
        upsert=True,
    )


def user_job_exists(clerk_user_id: str, url: str) -> bool:
    return _get_db()["user_jobs"].find_one({"clerk_user_id": clerk_user_id, "url": url}) is not None
