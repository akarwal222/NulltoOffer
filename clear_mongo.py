import os
from database import get_db
from dotenv import load_dotenv
load_dotenv()
db = get_db()
deleted = db.user_jobs.delete_many({}).deleted_count
print(f"✅ Cleared {deleted} user_job records. Jobs collection and user profiles untouched.")
