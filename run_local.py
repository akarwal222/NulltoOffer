import logging
import sys
from datetime import date
from dotenv import load_dotenv

# Must run before the imports below, they read env vars at import time
load_dotenv()

from core.gap_analyser import analyse_gaps
from core.scorer import CV_PATH, score_jobs
from emailer.digest import _build_html, send_digest
from scrapers.jobs_scraper import scrape_jobs
from scrapers.linkedin_posts_scraper import scrape_linkedin_posts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GetHired")

# Each term is "<role> <city>". Cities must exist in the scraper city maps,
# otherwise they fall back to London.
SEARCH_TERMS = ["ML Engineer London", "AI Engineer Remote"]
LOCATIONS = ["London", "Remote"]
VISA_STATUS = "UK Citizen / Indefinite Leave to Remain"
USE_POSTS = True
SEND_EMAIL = False
OUTPUT_FILE = "digest.html"


def main():
    if not CV_PATH.exists() or len(CV_PATH.read_text(encoding="utf-8").strip()) < 100:
        logger.error("Put your CV as plain text in %s before running", CV_PATH)
        sys.exit(1)

    jobs = scrape_jobs(SEARCH_TERMS)
    if USE_POSTS:
        jobs += scrape_linkedin_posts(SEARCH_TERMS)
    logger.info("%d jobs scraped", len(jobs))

    matches = score_jobs(jobs, locations=LOCATIONS, visa_status=VISA_STATUS)
    matches = analyse_gaps(matches)
    matches.sort(key=lambda j: j.get("score", 0), reverse=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(_build_html(matches, date.today().strftime("%Y-%m-%d")))
    logger.info("%d matches written to %s", len(matches), OUTPUT_FILE)

    if SEND_EMAIL:
        send_digest(matches)


if __name__ == "__main__":
    main()
