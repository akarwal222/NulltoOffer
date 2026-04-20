import logging
import os
import sys
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("GetHired")

_SCORE_THRESHOLD = 7
_MODEL = "claude-haiku-4-5-20251001"


def _score_job_for_user(job: dict, cv_text: str, client) -> dict | None:
    """Score a single job against an arbitrary cv_text; returns scored dict or None if below threshold."""
    import anthropic

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

    system_prompt = (
        "You are a recruitment analyst. Score job fit from 1–10 and assess visa sponsorship likelihood.\n\n"
        "LOCATION RULES:\n"
        "- Non-UK location, not remote: score 3 or below.\n"
        "- Remote with no UK mention: score on technical fit, Sponsor Likelihood = Unlikely.\n"
        "- Location unspecified: score normally, note 'Location unconfirmed — verify before applying'.\n"
        "- London or UK: score normally.\n\n"
        f"CANDIDATE CV:\n{cv_text}"
    )
    user_prompt = (
        f"{job_text}\n\n"
        "Respond in exactly this format (3 lines only):\n"
        "Score: <1-10>\n"
        "Reason: <2 lines max>\n"
        "Sponsor Likelihood: <Likely|Unlikely|Unknown>"
    )

    try:
        message = client.messages.create(
            model=_MODEL,
            max_tokens=1000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        result = {"score": 0, "reason": "", "sponsor_likelihood": "Unknown"}
        for line in message.content[0].text.strip().splitlines():
            line = line.strip()
            if line.lower().startswith("score:"):
                try:
                    result["score"] = int(line.split(":", 1)[1].strip().split("/")[0])
                except ValueError:
                    pass
            elif line.lower().startswith("reason:"):
                result["reason"] = line.split(":", 1)[1].strip()
            elif line.lower().startswith("sponsor"):
                val = line.split(":", 1)[1].strip()
                if val in ("Likely", "Unlikely", "Unknown"):
                    result["sponsor_likelihood"] = val
        if result["score"] >= _SCORE_THRESHOLD:
            return {**job, **result}
    except Exception as exc:
        logger.warning("Per-user scoring failed for '%s': %s", job.get("title"), exc)
    return None


def _analyse_gaps_for_user(job: dict, cv_text: str, client) -> dict:
    """Run gap analysis for a single job against an arbitrary cv_text."""
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
        message = client.messages.create(
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
        logger.warning("Per-user gap analysis failed for '%s': %s", job.get("title"), exc)

    return gaps


def _send_notification_email(jobs: list[dict]) -> None:
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    webapp_url = os.environ.get("WEBAPP_URL", "https://nulltooffer.app")
    dashboard_url = f"{webapp_url}/dashboard"
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")

    n = len(jobs)
    top4 = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)[:4]
    overflow = n - len(top4)

    subject = f"🎯 NullToOffer — {n} new match{'es' if n != 1 else ''} today"

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
    overflow_text = f"\n+ {overflow} more match{'es' if overflow != 1 else ''} waiting." if overflow > 0 else ""

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

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = gmail_address
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, gmail_address, msg.as_string())

    logger.info("Notification email sent (%d matches)", n)


def main():
    import anthropic
    from scrapers.jobs_scraper import scrape_jobs
    from scrapers.linkedin_posts_scraper import scrape_linkedin_posts
    # Indeed scraper removed — $6/1k too costly on free tier. Re-add with misceres/indeed-scraper when on paid plan.
    from core.deduplicator import deduplicate, mark_seen
    from core.scorer import score_jobs
    from core.gap_analyser import analyse_gaps
    from emailer.digest import send_digest
    from database import (
        get_all_users, save_job, save_user_job, user_job_exists,
    )

    # ── 1. Scrape LinkedIn structured listings ───────────────────────────────
    print("\n[1/8] Scraping LinkedIn job listings...")
    try:
        listings = scrape_jobs()
        print(f"      → {len(listings)} listings retrieved")
    except Exception as exc:
        logger.error("LinkedIn jobs scraper failed: %s", exc)
        listings = []

    # ── 2. Scrape LinkedIn posts ─────────────────────────────────────────────
    print("\n[2/8] Scraping LinkedIn hiring posts...")
    try:
        posts = scrape_linkedin_posts()
        print(f"      → {len(posts)} posts retrieved")
    except Exception as exc:
        logger.error("LinkedIn posts scraper failed: %s", exc)
        posts = []

    # ── 3. Merge ─────────────────────────────────────────────────────────────
    print("\n[3/8] Merging sources...")
    all_jobs = listings + posts
    print(f"      → {len(all_jobs)} total jobs")

    # ── 4. Deduplicate ────────────────────────────────────────────────────────
    print("\n[4/8] Deduplicating against seen jobs...")
    try:
        new_jobs = deduplicate(all_jobs)
        print(f"      → {len(new_jobs)} new (unseen) jobs")
    except Exception as exc:
        logger.error("Deduplication failed: %s", exc)
        new_jobs = all_jobs

    # ── 5. Score ──────────────────────────────────────────────────────────────
    print("\n[5/8] Scoring jobs with Claude Haiku...")
    try:
        scored = score_jobs(new_jobs)
        print(f"      → {len(scored)} jobs passed the score threshold (≥7/10)")
    except Exception as exc:
        logger.error("Scoring failed: %s", exc)
        scored = []

    # ── 6. Gap analysis ───────────────────────────────────────────────────────
    print("\n[6/8] Running gap analysis on top matches...")
    try:
        analysed = analyse_gaps(scored)
        print(f"      → Gap analysis complete for {len(analysed)} jobs")
    except Exception as exc:
        logger.error("Gap analysis failed: %s", exc)
        analysed = scored

    # ── 7. Write to MongoDB ───────────────────────────────────────────────────
    print("\n[7/8] Writing to MongoDB...")
    try:
        users = get_all_users()
        print(f"      → {len(users)} registered user(s) found")

        jobs_saved = 0
        for job in analysed:
            save_job(job)
            jobs_saved += 1

        user_jobs_written = 0
        if users:
            ai_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            for user in users:
                clerk_id = user.get("clerk_user_id", "")
                cv_text = user.get("cv_text", "")
                if not clerk_id or not cv_text:
                    continue
                for job in analysed:
                    url = job.get("url", "")
                    if not url or user_job_exists(clerk_id, url):
                        continue
                    scored_job = _score_job_for_user(job, cv_text, ai_client)
                    if scored_job is None:
                        continue
                    gaps = _analyse_gaps_for_user(scored_job, cv_text, ai_client)
                    save_user_job({
                        "clerk_user_id": clerk_id,
                        "url": url,
                        "score": scored_job.get("score", 0),
                        "reason": scored_job.get("reason", ""),
                        "sponsor_likelihood": scored_job.get("sponsor_likelihood", "Unknown"),
                        "hard_gaps": gaps.get("hard_gaps", []),
                        "soft_gaps": gaps.get("soft_gaps", []),
                        "hidden_strengths": gaps.get("hidden_strengths", []),
                        "created_at": datetime.now(timezone.utc),
                    })
                    user_jobs_written += 1

        print(f"      → {jobs_saved} job(s) saved, {user_jobs_written} user_job record(s) written")
    except Exception as exc:
        logger.error("MongoDB write failed: %s", exc)

    # ── 8. Email ──────────────────────────────────────────────────────────────
    print("\n[8/8] Sending email...")
    try:
        if os.environ.get("WEBAPP_URL"):
            _send_notification_email(analysed)
        else:
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
