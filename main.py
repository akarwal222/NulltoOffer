import logging
import sys
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GetHired")


def main():
    from scrapers.jobs_scraper import scrape_jobs
    from scrapers.linkedin_posts_scraper import scrape_linkedin_posts
    # Indeed scraper removed — $6/1k too costly on free tier. Re-add with misceres/indeed-scraper when on paid plan.
    from core.deduplicator import deduplicate, mark_seen
    from core.scorer import score_jobs
    from core.gap_analyser import analyse_gaps
    from emailer.digest import send_digest

    # ── 1. Scrape LinkedIn structured listings ───────────────────────────────
    print("\n[1/7] Scraping LinkedIn job listings...")
    try:
        listings = scrape_jobs()
        print(f"      → {len(listings)} listings retrieved")
    except Exception as exc:
        logger.error("LinkedIn jobs scraper failed: %s", exc)
        listings = []

    # ── 2. Scrape LinkedIn posts ─────────────────────────────────────────────
    print("\n[2/7] Scraping LinkedIn hiring posts...")
    try:
        posts = scrape_linkedin_posts()
        print(f"      → {len(posts)} posts retrieved")
    except Exception as exc:
        logger.error("LinkedIn posts scraper failed: %s", exc)
        posts = []

    # ── 3. Merge ─────────────────────────────────────────────────────────────
    print("\n[3/7] Merging sources...")
    all_jobs = listings + posts
    print(f"      → {len(all_jobs)} total jobs")

    # ── 4. Deduplicate ────────────────────────────────────────────────────────
    print("\n[4/7] Deduplicating against seen jobs...")
    try:
        new_jobs = deduplicate(all_jobs)
        print(f"      → {len(new_jobs)} new (unseen) jobs")
    except Exception as exc:
        logger.error("Deduplication failed: %s", exc)
        new_jobs = all_jobs

    # ── 5. Score ──────────────────────────────────────────────────────────────
    print("\n[5/7] Scoring jobs with Claude Haiku...")
    try:
        scored = score_jobs(new_jobs)
        print(f"      → {len(scored)} jobs passed the score threshold (≥7/10)")
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        scored = []

    # ── 6. Gap analysis ───────────────────────────────────────────────────────
    print("\n[6/7] Running gap analysis on top matches...")
    try:
        analysed = analyse_gaps(scored)
        print(f"      → Gap analysis complete for {len(analysed)} jobs")
    except Exception as exc:
        logger.error("Gap analysis failed: %s", exc)
        analysed = scored

    # ── 7. Email digest ───────────────────────────────────────────────────────
    print("\n[7/7] Sending email digest...")
    try:
        send_digest(analysed)
        print("      → Email sent successfully")
    except Exception as exc:
        logger.error("Email send failed: %s", exc)

    # ── Mark all new jobs as seen ─────────────────────────────────────────────
    try:
        mark_seen(new_jobs)
    except Exception as exc:
        logger.error("Failed to update seen_jobs.json: %s", exc)

    print("\n✓ GetHired pipeline complete.\n")


if __name__ == "__main__":
    main()
