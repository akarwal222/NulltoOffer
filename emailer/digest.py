import os
import re
import resend
import logging
from datetime import date

resend.api_key = os.getenv("RESEND_API_KEY")

logger = logging.getLogger(__name__)

_CSS = """
  body { margin: 0; padding: 0; background: #f3f4f6; font-family: Arial, sans-serif; color: #1a1a2e; }
  .wrapper { max-width: 600px; margin: 0 auto; padding: 24px 16px; }
  .header { text-align: center; padding: 28px 0 20px; }
  .header h1 { margin: 0; font-size: 22px; color: #0f172a; }
  .header p { margin: 6px 0 0; font-size: 13px; color: #6b7280; }
  .card { background: #ffffff; border: 1px solid #d0d8e8; border-radius: 8px; padding: 16px; margin-bottom: 20px; }
  .job-title { font-size: 16px; font-weight: bold; color: #0f172a; margin: 0 0 6px; }
  .meta { font-size: 13px; color: #6b7280; margin: 0 0 10px; }
  .badges { margin-bottom: 10px; }
  .badge { display: inline-block; font-size: 12px; font-weight: bold; color: #ffffff; padding: 3px 10px; border-radius: 12px; margin-right: 6px; }
  .badge-green { background: #16a34a; }
  .badge-blue { background: #2563eb; }
  .sponsor { font-size: 12px; color: #6b7280; font-style: italic; vertical-align: middle; }
  .why { font-size: 13px; margin: 8px 0; }
  .why strong { color: #1a1a2e; }
  .gaps { margin: 10px 0; }
  .gaps-label { font-size: 13px; font-weight: bold; color: #374151; margin: 8px 0 4px; }
  .gap-item { font-size: 13px; color: #374151; margin: 2px 0 2px 12px; }
  .action-box { background: #fef9c3; border-left: 3px solid #f59e0b; color: #854d0e; font-size: 13px; padding: 10px 12px; border-radius: 4px; margin: 12px 0 0; }
  .apply-btn { display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; font-size: 13px; font-weight: bold; padding: 8px 16px; border-radius: 6px; margin-top: 12px; }
  hr { border: none; border-top: 1px solid #e5e7eb; margin: 4px 0 24px; }
  .footer { text-align: center; font-size: 11px; color: #9ca3af; padding: 12px 0; }
"""


def _md_to_html(text: str) -> str:
    """Convert **bold** and *italic* markdown to HTML tags."""
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    return text


def _md_to_plain(text: str) -> str:
    """Strip markdown asterisks for plain text fallback."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    return text


def _location_display(location: str) -> str:
    if not location:
        return ""
    if "remote" in location.lower():
        return f"🌍 Remote · {location}" if location.lower() != "remote" else "🌍 Remote"
    return location


def _score_badge(score: int) -> str:
    css_class = "badge-green" if score >= 9 else "badge-blue"
    return f'<span class="badge {css_class}">{score}/10</span>'


def _gap_section_html(label: str, items: list[str]) -> str:
    if not items:
        return ""
    rows = "".join(f'<div class="gap-item">{label.split()[0]} {_md_to_html(item)}</div>' for item in items)
    return f'<div class="gaps-label">{label}</div>{rows}'


def _format_job_html(job: dict) -> str:
    title = job.get("title") or "Unknown Role"
    company = job.get("company") or "Unknown Company"
    location = _location_display(job.get("location") or "")
    salary = job.get("salary") or ""
    score = job.get("score", 0)
    reason = _md_to_html(job.get("reason") or "")
    sponsor = job.get("sponsor_likelihood") or "Unknown"
    url = job.get("url") or ""
    gaps = job.get("gap_analysis") or {}

    meta_parts = [p for p in [company, location, salary] if p]
    meta = " &middot; ".join(meta_parts)

    hard_html = _gap_section_html("🔴 Hard Gaps", gaps.get("hard_gaps", []))
    soft_html = _gap_section_html("🟡 Soft Gaps", gaps.get("soft_gaps", []))
    strengths_html = _gap_section_html("🟢 Hidden Strengths", gaps.get("hidden_strengths", []))
    gaps_html = hard_html + soft_html + strengths_html

    if job.get("type") == "informal_post":
        poster_name = job.get("poster_name") or "the poster"
        poster_title = job.get("poster_title") or ""
        poster_ref = f"{poster_name} ({poster_title})" if poster_title else poster_name
        no_action_html = (
            '<div style="background:#fef2f2;border-left:3px solid #ef4444;color:#991b1b;'
            'font-size:12px;padding:8px 12px;border-radius:4px;margin-top:8px;">'
            '⚠️ No direct apply action found — verify before reaching out.'
            '</div>'
        ) if (job.get("apply_action") or "") == "none" else ""
        cta_html = (
            f'<div class="action-box">'
            f'<strong>📢 Action:</strong> DM {poster_ref} directly — do not use apply button'
            f'</div>'
            f'{no_action_html}'
            f'<div style="margin-top:8px;font-size:12px;color:#6b7280;">Post: <a href="{url}">{url}</a></div>'
        )
    else:
        cta_html = f'<a class="apply-btn" href="{url}">Apply &rarr;</a>'

    return f"""
<div class="card">
  <div class="job-title">{title}</div>
  <div class="meta">{meta}</div>
  <div class="badges">
    {_score_badge(score)}
    <span class="sponsor">Sponsor: {sponsor}</span>
  </div>
  <div class="why"><strong>Why:</strong> {reason}</div>
  <div class="gaps">{gaps_html}</div>
  {cta_html}
</div>"""


def _build_html(sorted_jobs: list[dict], today: str) -> str:
    n = len(sorted_jobs)
    if n == 0:
        body_content = "<p style='color:#6b7280;text-align:center;'>No new matches today. The pipeline ran successfully.</p>"
    else:
        cards = "<hr>".join(_format_job_html(j) for j in sorted_jobs)
        body_content = cards

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{_CSS}</style></head>
<body>
<div class="wrapper">
  <div class="header">
    <h1>🎯 NullToOffer — Job Digest</h1>
    <p>{today} &nbsp;&middot;&nbsp; {n} new match{"es" if n != 1 else ""}</p>
  </div>
  {body_content}
  <div class="footer">Powered by NullToOffer &nbsp;&middot;&nbsp; Unsubscribe</div>
</div>
</body>
</html>"""


def _format_job_text(job: dict) -> str:
    title = job.get("title") or "Unknown Role"
    company = job.get("company") or "Unknown Company"
    location = _location_display(job.get("location") or "")
    salary = job.get("salary") or ""
    score = job.get("score", 0)
    reason = _md_to_plain(job.get("reason") or "")
    sponsor = job.get("sponsor_likelihood") or "Unknown"
    url = job.get("url") or ""
    gaps = job.get("gap_analysis") or {}

    salary_part = f" | {salary}" if salary else ""
    lines = [
        f"{title} — {company} | {location}{salary_part}",
        f"Score: {score}/10  |  Sponsor: {sponsor}",
        f"Why: {reason}",
    ]

    hard = gaps.get("hard_gaps", [])
    soft = gaps.get("soft_gaps", [])
    strengths = gaps.get("hidden_strengths", [])

    if hard or soft or strengths:
        lines.append("Gap Analysis:")
    if hard:
        lines.append("  Hard Gaps:")
        lines.extend(f"    - {_md_to_plain(g)}" for g in hard)
    if soft:
        lines.append("  Soft Gaps:")
        lines.extend(f"    - {_md_to_plain(g)}" for g in soft)
    if strengths:
        lines.append("  Hidden Strengths:")
        lines.extend(f"    - {_md_to_plain(g)}" for g in strengths)

    if job.get("type") == "informal_post":
        poster_name = job.get("poster_name") or "the poster"
        poster_title = job.get("poster_title") or ""
        poster_ref = f"{poster_name} ({poster_title})" if poster_title else poster_name
        lines.append(f"Action: DM {poster_ref} directly — do not use apply button")
        if (job.get("apply_action") or "") == "none":
            lines.append("⚠️ No direct apply action found — verify before reaching out.")
        lines.append(f"Post: {url}")
    else:
        lines.append(f"Apply: {url}")

    return "\n".join(lines)


def send_digest(jobs: list[dict]) -> None:
    recipient_email = os.getenv("FROM_EMAIL", "hello@nulltooffer.app")
    today = date.today().strftime("%Y-%m-%d")

    sorted_jobs = sorted(jobs, key=lambda j: j.get("score", 0), reverse=True)
    n = len(sorted_jobs)

    subject = f"🎯 Job Digest — {today} — {n} new matches"

    html_content = _build_html(sorted_jobs, today)

    if n == 0:
        plain_content = f"NullToOffer Job Digest — {today}\n\nNo new matches today. The pipeline ran successfully."
    else:
        separator = "\n" + "-" * 60 + "\n"
        plain_content = f"NullToOffer Job Digest — {today} — {n} new matches\n"
        plain_content += separator + separator.join(_format_job_text(j) for j in sorted_jobs)

    params = {
        "from": f"NullToOffer <{recipient_email}>",
        "to": [recipient_email],
        "subject": subject,
        "html": html_content,
        "text": plain_content,
    }
    resend.Emails.send(params)

    logger.info("Email digest sent to %s (%d jobs)", recipient_email, n)
