import asyncio
import logging
import os
import sys
import resend
import requests
import sentry_sdk
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")

sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN_PIPELINE"),
    traces_sample_rate=0.1,
    environment=os.getenv("ENVIRONMENT", "production"),
    send_default_pii=False,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GetHired")

_SCORE_THRESHOLD = 7
_MODEL = "claude-haiku-4-5-20251001"

_LOCATION_CITY_MAP = [
    ("new york", "New York"),
    ("san francisco", "San Francisco"),
    ("london", "London"),
    ("delhi", "Delhi"),
    ("mumbai", "Mumbai"),
    ("bangalore", "Bangalore"),
]


def _extract_city(location: str) -> str:
    loc_lower = location.lower().strip()
    if loc_lower.startswith("remote"):
        return "Remote"
    for key, city in _LOCATION_CITY_MAP:
        if key in loc_lower:
            return city
    if "," in location:
        return location.split(",")[0].strip()
    return location.strip()


def _build_search_terms(users: list[dict]) -> list[str]:
    from collections import Counter
    term_counts: Counter = Counter()
    for user in users:
        roles = user.get("target_roles") or []
        locations = user.get("locations") or []
        for role in roles:
            for location in locations:
                city = _extract_city(location)
                term_counts[f"{role} {city}"] += 1
    sorted_terms = [t for t, _ in term_counts.most_common()]
    return sorted_terms[:20]


async def _score_job_for_user(
    job: dict,
    cv_text: str,
    client,
    locations: list[str] | None = None,
    visa_status: str = "",
) -> dict | None:
    from core.scorer import _build_location_rules, _build_sponsor_rules, _build_experience_rules, _parse_response, _is_commentary_post

    if _is_commentary_post(job):
        return None

    parts = [
        f"Title: {job.get('title', '')}",
        f"Company: {job.get('company', '')}",
        f"Location: {job.get('location', '')}",
    ]
    if job.get("salary"):
        parts.append(f"Salary: {job['salary']}")
    if job.get("type") == "informal_post":
        parts.append(f"Post text: {job.get('post_text', '')}")
    else:
        parts.append(f"Description: {job.get('description', '')}")
    job_text = "\n".join(parts)

    location_rules = _build_location_rules(locations or [])
    sponsor_rules, sponsor_hint = _build_sponsor_rules(visa_status)
    experience_rules = _build_experience_rules()

    system_prompt = (
        "You are a recruitment analyst. Score job fit from 1–10 and assess visa sponsorship likelihood.\n\n"
        f"{location_rules}\n\n"
        f"{sponsor_rules}\n\n"
        f"{experience_rules}\n\n"
        f"CANDIDATE CV:\n{cv_text}"
    )
    user_prompt = (
        f"{job_text}\n\n"
        "Respond in exactly this format (3 lines only):\n"
        "Score: <1-10>\n"
        "Reason: <2 lines max>\n"
        f"Sponsor Likelihood: <{sponsor_hint}>"
    )

    try:
        message = await client.messages.create(
            model=_MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        result = _parse_response(message.content[0].text)
        if result["score"] >= _SCORE_THRESHOLD:
            return {**job, **result}
    except Exception as exc:
        logger.warning("Scoring failed for '%s': %s", job.get("title"), exc)
    return None


async def _analyse_gap_for_job(job: dict, cv_text: str, client) -> dict:
    system_prompt = (
        "You are a career coach performing CV gap analysis. "
        "Compare a job description against the candidate's CV and identify gaps and strengths.\n\n"
        f"CANDIDATE CV:\n{cv_text}"
    )
    job_desc = job.get("description") or job.get("post_text") or ""
    user_prompt = (
        f"Job Title: {job.get('title', '')}\n"
        f"Company: {job.get('company', '')}\n"
        f"Description:\n{job_desc}\n\n"
        "Respond in exactly this format. Max 3 bullets per section.\n"
        "Hard Gaps:\n- 🔴 <item>\n\nSoft Gaps:\n- 🟡 <item>\n\nHidden Strengths:\n- 🟢 <item>"
    )

    gaps = {"hard_gaps": [], "soft_gaps": [], "hidden_strengths": []}
    try:
        message = await client.messages.create(
            model=_MODEL,
            max_tokens=1500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        current = None
        for line in message.content[0].text.strip().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            lower = stripped.lower()
            if "hard gap" in lower:
                current = "hard_gaps"
            elif "soft gap" in lower:
                current = "soft_gaps"
            elif "hidden strength" in lower:
                current = "hidden_strengths"
            elif stripped.startswith("-") and current:
                item = stripped.lstrip("- ").strip()
                for emoji in ("🔴 ", "🟡 ", "🟢 ", "🔴", "🟡", "🟢"):
                    if item.startswith(emoji):
                        item = item[len(emoji):].strip()
                        break
                if item and len(gaps[current]) < 3:
                    gaps[current].append(item)
    except Exception as exc:
        logger.warning("Gap analysis failed for '%s': %s", job.get("title"), exc)

    return gaps


def filter_jobs_for_user(jobs: list[dict], target_roles: list[str]) -> list[dict]:
    if len(jobs) < 50:
        return jobs

    role_keywords = {word.lower() for role in target_roles for word in role.split()}

    result = []
    for job in jobs:
        if job.get("type") == "listing":
            result.append(job)
        else:
            title = (job.get("title") or "").lower()
            if any(kw in title for kw in role_keywords):
                result.append(job)
    return result


async def score_jobs_for_user(
    jobs: list[dict],
    cv_text: str,
    locations: list[str],
    visa_status: str,
    requires_sponsorship: bool,
) -> list[dict]:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    scored = []
    for job in jobs:
        result = await _score_job_for_user(job, cv_text, client, locations, visa_status)
        if result is not None:
            scored.append(result)
        await asyncio.sleep(0.3)
    return scored


async def analyse_gaps_for_user(jobs: list[dict], cv_text: str) -> list[dict]:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    results = []
    for job in jobs:
        gaps = await _analyse_gap_for_job(job, cv_text, client)
        results.append({**job, "gap_analysis": gaps})
        await asyncio.sleep(0.3)
    return results


def write_user_jobs_to_mongo(jobs: list[dict], clerk_user_id: str) -> None:
    from database import save_job, save_user_job
    for job in jobs:
        url = job.get("url", "")
        if not url:
            continue
        save_job(job)
        gap_analysis = job.get("gap_analysis", {})
        save_user_job({
            "clerk_user_id": clerk_user_id,
            "url": url,
            "score": job.get("score", 0),
            "reason": job.get("reason", ""),
            "sponsor_likelihood": job.get("sponsor_likelihood", "Unknown"),
            "hard_gaps": gap_analysis.get("hard_gaps", []),
            "soft_gaps": gap_analysis.get("soft_gaps", []),
            "hidden_strengths": gap_analysis.get("hidden_strengths", []),
            "created_at": datetime.now(timezone.utc),
        })


def get_user_email(clerk_user_id: str) -> str | None:
    clerk_secret = os.environ.get("CLERK_SECRET_KEY")
    if not clerk_secret:
        logger.warning("CLERK_SECRET_KEY not set — cannot fetch email for %s", clerk_user_id)
        return None
    try:
        resp = requests.get(
            f"https://api.clerk.com/v1/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {clerk_secret}"},
            timeout=10,
        )
        resp.raise_for_status()
        emails = resp.json().get("email_addresses", [])
        if emails:
            return emails[0].get("email_address")
    except Exception as exc:
        logger.warning("Failed to fetch email for %s: %s", clerk_user_id, exc)
    return None


def send_notification_email(jobs: list[dict], email: str, user: dict) -> None:
    if not jobs:
        return

    webapp_url = os.environ.get("WEBAPP_URL", "https://nulltooffer.app")
    dashboard_url = f"{webapp_url}/dashboard"
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    n = len(jobs)
    top4 = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:4]
    overflow = n - len(top4)

    subject = f"🎯 NullToOffer — {n} new match{'es' if n != 1 else ''} for you today"

    def _score_badge(score: int) -> str:
        return (
            f'<span style="display:inline-block;background:#f59e0b;color:#1a1a2e;'
            f'font-size:11px;font-weight:bold;padding:2px 9px;border-radius:10px;">'
            f'{score}/10</span>'
        )

    def _card_html(job: dict) -> str:
        title = job.get("title") or "Unknown Role"
        company = job.get("company") or ""
        location = job.get("location") or ""
        meta = " &middot; ".join(p for p in [company, location] if p)
        score = job.get("score", 0)
        return (
            f'<div style="background:#1e293b;border-radius:7px;padding:13px 16px;margin-bottom:10px;">'
            f'<div style="font-size:14px;font-weight:bold;color:#ffffff;margin-bottom:4px;">{title}</div>'
            f'<div style="font-size:12px;color:#94a3b8;margin-bottom:8px;">{meta}</div>'
            f'{_score_badge(score)}'
            f'</div>'
        )

    cards_html = "".join(_card_html(j) for j in top4)
    overflow_html = (
        f'<p style="font-size:13px;color:#6b7280;text-align:center;margin:4px 0 24px;">'
        f'+ {overflow} more match{"es" if overflow != 1 else ""} on your dashboard</p>'
        if overflow > 0 else
        '<div style="margin-bottom:24px;"></div>'
    )

    top4_text = "\n".join(
        f"  {i+1}. {j.get('title','')} — {j.get('company','')}  [{j.get('score',0)}/10]"
        for i, j in enumerate(top4)
    )
    overflow_text = (
        f"\n+ {overflow} more match{'es' if overflow != 1 else ''} waiting."
        if overflow > 0 else ""
    )

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;color:#1a1a2e;">
<div style="max-width:600px;margin:0 auto;padding:32px 16px;">

  <div style="text-align:center;padding:24px 0 20px;">
    <div style="font-size:24px;font-weight:bold;color:#f59e0b;margin-bottom:4px;">🎯 NullToOffer</div>
    <div style="font-size:13px;color:#6b7280;">{today}</div>
  </div>

  <p style="font-size:15px;color:#1a1a2e;text-align:center;margin:0 0 24px;">
    You have <strong>{n} new job match{'es' if n != 1 else ''}</strong> waiting for you today.
  </p>

  {cards_html}
  {overflow_html}

  <div style="text-align:center;margin-bottom:32px;">
    <a href="{dashboard_url}"
       style="display:inline-block;background:#f59e0b;color:#1a1a2e;text-decoration:none;
              font-size:16px;font-weight:bold;padding:16px 40px;border-radius:8px;">
      View All Matches &rarr;
    </a>
  </div>

  <div style="text-align:center;font-size:11px;color:#9ca3af;padding-bottom:16px;">
    Powered by NullToOffer &nbsp;&middot;&nbsp; {webapp_url.removeprefix("https://").removeprefix("http://")}
  </div>

</div>
</body>
</html>"""

    plain_body = (
        f"NullToOffer — {n} new match{'es' if n != 1 else ''} today ({today})\n\n"
        f"Top matches:\n{top4_text}{overflow_text}\n\n"
        f"View all {n} match{'es' if n != 1 else ''} at {dashboard_url}"
    )

    params = {
        "from": f"NullToOffer <{os.getenv('FROM_EMAIL', 'hello@nulltooffer.app')}>",
        "to": [email],
        "subject": subject,
        "html": html_body,
        "text": plain_body,
    }
    resend.Emails.send(params)

    logger.info("Notification email sent to %s (%d matches)", email, n)


# Synchronous alias for use with asyncio.to_thread()
get_user_email_sync = get_user_email


def send_waitlist_email(email: str) -> None:
    webapp_url = os.environ.get("WEBAPP_URL", "https://nulltooffer.app")
    site_label = webapp_url.removeprefix("https://").removeprefix("http://")

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,sans-serif;color:#1a1a2e;">
<div style="max-width:600px;margin:0 auto;padding:32px 16px;">

  <div style="text-align:center;padding:24px 0 20px;">
    <div style="font-size:24px;font-weight:bold;color:#f59e0b;margin-bottom:4px;">🎯 NullToOffer</div>
  </div>

  <div style="background:#1e293b;border-radius:10px;padding:28px 32px;margin-bottom:24px;">
    <h2 style="color:#f59e0b;margin:0 0 16px;font-size:20px;">Your free trial has ended</h2>
    <p style="color:#e2e8f0;font-size:15px;line-height:1.6;margin:0 0 16px;">
      Thanks for trying NullToOffer — your free trial run has been used.
    </p>
    <p style="color:#94a3b8;font-size:14px;line-height:1.6;margin:0 0 16px;">
      You're now on the waitlist for full access. We'll reach out as soon as a spot opens up.
      In the meantime, all your job matches from your trial are still waiting on your dashboard.
    </p>
    <p style="color:#94a3b8;font-size:14px;line-height:1.6;margin:0;">
      Want to move faster? Just reply to this email and we'll see what we can do.
    </p>
  </div>

  <div style="text-align:center;margin-bottom:32px;">
    <a href="{webapp_url}/dashboard"
       style="display:inline-block;background:#f59e0b;color:#1a1a2e;text-decoration:none;
              font-size:15px;font-weight:bold;padding:14px 36px;border-radius:8px;">
      View Your Dashboard &rarr;
    </a>
  </div>

  <div style="text-align:center;font-size:11px;color:#9ca3af;padding-bottom:16px;">
    Powered by NullToOffer &nbsp;&middot;&nbsp; {site_label}
  </div>

</div>
</body>
</html>"""

    plain_body = (
        "Your NullToOffer free trial has ended.\n\n"
        "You're now on the waitlist for full access. We'll reach out as soon as a spot opens up.\n"
        "All your job matches from your trial are still on your dashboard.\n\n"
        f"View your dashboard: {webapp_url}/dashboard\n\n"
        "Want to move faster? Just reply to this email."
    )

    params = {
        "from": f"NullToOffer <{os.getenv('FROM_EMAIL', 'hello@nulltooffer.app')}>",
        "to": [email],
        "subject": "Your NullToOffer trial has ended - you're on the waitlist",
        "html": html_body,
        "text": plain_body,
    }
    resend.Emails.send(params)
    logger.info("Waitlist email sent to %s", email)


async def process_user(user: dict) -> int:
    from database import user_job_exists, get_db
    from scrapers.jobs_scraper import scrape_jobs
    from scrapers.linkedin_posts_scraper import scrape_linkedin_posts

    clerk_user_id = user["clerk_user_id"]
    short_id = f"{clerk_user_id[:8]}..."
    cv_text = user.get("cv_text", "")
    target_roles = user.get("target_roles", [])
    locations = user.get("locations", [])
    visa_status = user.get("visa_status", "")
    requires_sponsorship = user.get("requires_sponsorship", False)

    # ── Subscription / trial gate ─────────────────────────────────────────────
    # To grant Pro access manually: update user document in MongoDB Atlas
    # db.users.update_one({"clerk_user_id": "user_..."}, {"$set": {"subscription_tier": "pro"}})
    subscription_tier = user.get("subscription_tier", "free")
    trial_used = user.get("trial_used", False)

    if subscription_tier in ("pro", "lite"):
        logger.info("User %s: %s subscription - processing", clerk_user_id[:8], subscription_tier)
    elif subscription_tier == "free":
        if not trial_used:
            logger.info("User %s: free trial run", clerk_user_id[:8])
            db = get_db()
            db.users.update_one(
                {"clerk_user_id": clerk_user_id},
                {"$set": {"trial_used": True, "trial_started_at": datetime.utcnow().isoformat()}},
            )
        else:
            logger.info("User %s: free trial exhausted - skipping", clerk_user_id[:8])
            if not user.get("waitlist_email_sent", False):
                email = await asyncio.to_thread(get_user_email_sync, clerk_user_id)
                if email:
                    await asyncio.to_thread(send_waitlist_email, email)
                    db = get_db()
                    db.users.update_one(
                        {"clerk_user_id": clerk_user_id},
                        {"$set": {"waitlist_email_sent": True}},
                    )
            return 0

    if not cv_text:
        logger.warning("User %s has no CV uploaded — skipping", clerk_user_id)
        return 0

    user_search_terms = _build_search_terms([user])

    preferred_sources = user.get("preferred_sources", ["linkedin_jobs", "linkedin_posts"])
    listings = []
    posts = []

    if "linkedin_jobs" in preferred_sources:
        listings = await asyncio.to_thread(scrape_jobs, user_search_terms)
    if "linkedin_posts" in preferred_sources:
        posts = await asyncio.to_thread(scrape_linkedin_posts, user_search_terms)

    all_jobs = listings + posts
    logger.info("User %s: sources %s → %d jobs scraped", clerk_user_id[:8], preferred_sources, len(all_jobs))

    relevant_jobs = filter_jobs_for_user(all_jobs, target_roles)

    # Per-user deduplication: skip jobs this user has already seen
    jobs_to_score = [
        j for j in relevant_jobs
        if j.get("url") and not user_job_exists(clerk_user_id, j["url"])
    ]

    scored_jobs = await score_jobs_for_user(jobs_to_score, cv_text, locations, visa_status, requires_sponsorship)
    analysed_jobs = await analyse_gaps_for_user(scored_jobs, cv_text)
    write_user_jobs_to_mongo(analysed_jobs, clerk_user_id)

    n = len(analysed_jobs)

    if n == 0:
        logger.info("→ User %s: 0 matches — no jobs above threshold", short_id)
        return 0

    email = await asyncio.to_thread(get_user_email, clerk_user_id)
    if email:
        await asyncio.to_thread(send_notification_email, analysed_jobs, email, user)
        logger.info("→ User %s: %d match%s written, email sent to %s", short_id, n, "es" if n != 1 else "", email)
    else:
        logger.warning("Could not retrieve email for user %s — skipping notification", clerk_user_id)
        logger.info("→ User %s: %d match%s written, no email sent", short_id, n, "es" if n != 1 else "")

    return n


async def main():
    with sentry_sdk.start_transaction(op="pipeline", name="NullToOffer Daily Scan"):
        from database import get_all_users

        # ── Step 0: Load all users from MongoDB ──────────────────────────────────
        print("\n[Step 0] Loading users from MongoDB...")
        try:
            users = get_all_users()
        except Exception as exc:
            logger.error("MongoDB connection failed: %s", exc)
            sys.exit(1)

        if not users:
            logger.info("No registered users found — skipping pipeline run")
            sys.exit(0)

        print(f"         → {len(users)} user(s) loaded")

        # ── Step 1: Build combined search terms ──────────────────────────────────
        print("\n[Step 1] Building combined search terms from user preferences...")
        valid_users: list[dict] = []
        for user in users:
            clerk_id = user.get("clerk_user_id", "unknown")
            if not user.get("target_roles") or not user.get("locations"):
                logger.warning("User %s has incomplete preferences — skipping.", clerk_id)
            else:
                valid_users.append(user)

        if not valid_users:
            logger.info("No users with complete preferences — skipping pipeline run")
            sys.exit(0)

        # ── Steps 2-5: Per-user parallel processing ───────────────────────────────
        print(f"\n[Steps 2-5] Processing {len(valid_users)} user(s) in parallel (max 3 concurrent)...")

        semaphore = asyncio.Semaphore(3)

        async def process_with_semaphore(user: dict) -> int:
            async with semaphore:
                return await process_user(user)

        results = await asyncio.gather(
            *[process_with_semaphore(u) for u in valid_users],
            return_exceptions=True,
        )

        # ── Summary log ──────────────────────────────────────────────────────────
        total_jobs_written = 0
        skipped_users: list[str] = []
        for user, result in zip(valid_users, results):
            clerk_id = user.get("clerk_user_id", "unknown")
            if isinstance(result, Exception):
                logger.error("Processing failed for user %s: %s", clerk_id, result)
                skipped_users.append(clerk_id)
            else:
                total_jobs_written += result

        processed = len(valid_users) - len(skipped_users)
        print(f"\n[Summary]")
        print(f"  Users processed:    {processed}/{len(valid_users)}")
        print(f"  Total jobs written: {total_jobs_written}")
        if skipped_users:
            print(f"  Skipped (errors):   {', '.join(skipped_users)}")

        print("\n✓ NullToOffer pipeline complete.\n")


if __name__ == "__main__":
    asyncio.run(main())
