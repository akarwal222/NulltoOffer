import os
import smtplib
import logging
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _format_job(job: dict) -> str:
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    location = job.get("location", "")
    salary = job.get("salary", "")
    score = job.get("score", "?")
    reason = job.get("reason", "")
    sponsor = job.get("sponsor_likelihood", "Unknown")
    url = job.get("url", "")
    gaps = job.get("gap_analysis", {})

    salary_part = f" | {salary}" if salary else ""
    header = f"{title} — {company} | {location}{salary_part}"
    relevance = f"Relevance: {score}/10 | Sponsor Licence: {sponsor}"
    why = f"Why: {reason}"

    hard = gaps.get("hard_gaps", [])
    soft = gaps.get("soft_gaps", [])
    strengths = gaps.get("hidden_strengths", [])

    gap_lines = ["Gap Analysis:"]
    if hard:
        gap_lines.append("  🔴 Hard Gaps:")
        gap_lines.extend(f"     • {g}" for g in hard)
    if soft:
        gap_lines.append("  🟡 Soft Gaps:")
        gap_lines.extend(f"     • {g}" for g in soft)
    if strengths:
        gap_lines.append("  🟢 Hidden Strengths:")
        gap_lines.extend(f"     • {g}" for g in strengths)

    if job.get("type") == "informal_post":
        poster_name = job.get("poster_name", "the poster")
        poster_title = job.get("poster_title", "")
        poster_ref = f"{poster_name} ({poster_title})" if poster_title else poster_name
        action = f"Action: DM {poster_ref} directly — do not use apply button\n  Post: {url}"
    else:
        action = f"Apply: {url}"

    return "\n".join([header, relevance, why, *gap_lines, action])


def send_digest(jobs: list[dict]) -> None:
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_password = os.environ["GMAIL_APP_PASSWORD"]
    today = date.today().strftime("%Y-%m-%d")

    sorted_jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)

    subject = f"🎯 Job Digest — {today} — {len(sorted_jobs)} new matches"

    if not sorted_jobs:
        body = f"Job Digest — {today}\n\nNo new matches today. The pipeline ran successfully."
    else:
        separator = "\n" + "─" * 60 + "\n"
        sections = [_format_job(j) for j in sorted_jobs]
        body = f"Job Digest — {today} — {len(sorted_jobs)} new matches\n\n"
        body += separator.join(sections)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = gmail_address
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(gmail_address, gmail_password)
        server.sendmail(gmail_address, gmail_address, msg.as_string())

    logger.info("Email digest sent to %s (%d jobs)", gmail_address, len(sorted_jobs))
