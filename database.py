import os
import threading
from datetime import datetime, timezone
from pymongo import MongoClient

_client = None
_db = None
_lock = threading.Lock()


def _get_db():
    global _client, _db
    with _lock:
        if _db is None:
            _client = MongoClient(
                os.environ["MONGODB_URI"],
                serverSelectionTimeoutMS=5000,
            )
            _client.admin.command("ping")
            _db = _client["nulltooffer"]
    return _db


def get_db():
    return _get_db()


def get_all_users() -> list[dict]:
    return list(_get_db()["users"].find({}, {"_id": 0}))


def job_exists(url: str) -> bool:
    return _get_db()["jobs"].find_one({"url": url}) is not None


def save_job(job: dict) -> None:
    job_data = {k: v for k, v in job.items() if k != "_id"}
    job_data["scraped_at"] = datetime.utcnow().isoformat()
    _get_db()["jobs"].update_one({"url": job["url"]}, {"$set": job_data}, upsert=True)


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
