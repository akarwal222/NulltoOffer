import json, os
path = os.path.join("data", "seen_jobs.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump({}, f)
print("✅ Cache cleared. Next run will treat all jobs as new.")
