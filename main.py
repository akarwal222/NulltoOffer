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
    from scrapers.indeed_scraper import scrape_indeed_jobs
    from core.deduplicator import deduplicate, mark_seen
    from core.scorer import score_jobs
    from core.gap_analyser import analyse_gaps
    from emailer.digest import send_digest

    # ── 1. Scrape LinkedIn structured listings ───────────────────────────────
    print("\n[1/8] Scraping LinkedIn job listings...")
    try:
        listings = scrape_jobs()
        print(f"      → {len(listings)} listings retrieved")
    except Exception as exc:
        logger.error("LinkedIn jobs scraper failed: %s", exc)
        listings = []

    # ── 2. Scrape Indeed listings ────────────────────────────────────────────
    print("\n[2/8] Scraping Indeed job listings...")
    try:
        indeed_listings = scrape_indeed_jobs()
        print(f"      → {len(indeed_listings)} listings retrieved")
    except Exception as exc:
        logger.error("Indeed scraper failed: %s", exc)
        indeed_listings = []

    # ── 3. Scrape LinkedIn posts ─────────────────────────────────────────────
    print("\n[3/8] Scraping LinkedIn hiring posts...")
    try:
        posts = scrape_linkedin_posts()
        print(f"      → {len(posts)} posts retrieved")
    except Exception as exc:
        logger.error("LinkedIn posts scraper failed: %s", exc)
        posts = []

    # ── 4. Merge ─────────────────────────────────────────────────────────────
    print("\n[4/8] Merging sources...")
    all_jobs = listings + indeed_listings + posts
    print(f"      → {len(all_jobs)} total jobs")

    # ── 5. Deduplicate ────────────────────────────────────────────────────────
    print("\n[5/8] Deduplicating against seen jobs...")
    try:
        new_jobs = deduplicate(all_jobs)
        print(f"      → {len(new_jobs)} new (unseen) jobs")
    except Exception as exc:
        logger.error("Deduplication failed: %s", exc)
        new_jobs = all_jobs

    # ── 6. Score ──────────────────────────────────────────────────────────────
    print("\n[6/8] Scoring jobs with Claude Haiku...")
    try:
        scored = score_jobs(new_jobs)
        print(f"      → {len(scored)} jobs passed the score threshold (≥7/10)")
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        scored = []

    # ── 7. Gap analysis ───────────────────────────────────────────────────────
    print("\n[7/8] Running gap analysis on top matches...")
    try:
        analysed = analyse_gaps(scored)
        print(f"      → Gap analysis complete for {len(analysed)} jobs")
    except Exception as exc:
        logger.error("Gap analysis failed: %s", exc)
        analysed = scored

    # ── 8. Email digest ───────────────────────────────────────────────────────
    print("\n[8/8] Sending email digest...")
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
