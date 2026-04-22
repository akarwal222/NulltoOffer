import os
import logging
import anthropic
from pathlib import Path

logger = logging.getLogger(__name__)

CV_PATH = Path(__file__).parent.parent / "data" / "my_cv.txt"
SCORE_THRESHOLD = 7
MODEL = "claude-haiku-4-5-20251001"

_COMMENTARY_KEYWORDS = frozenset({
    "journalist", "reporter", "newsletter", "writer", "editor", "media",
})

_NO_SPONSOR_STATUSES = frozenset({
    "UK Citizen / Indefinite Leave to Remain",
    "EU Settled / Pre-Settled Status",
})
_STRICT_SPONSOR_STATUSES = frozenset({
    "Require Sponsorship from employer",
})
_SWITCH_SPONSOR_STATUSES = frozenset({
    "Skilled Worker Visa (already sponsored)",
})
_VALID_SPONSOR = frozenset({"Likely", "Unlikely", "Unknown", "N/A"})


def _is_commentary_post(job: dict) -> bool:
    if job.get("type") != "informal_post":
        return False
    if job.get("apply_action") != "none":
        return False
    poster_title = (job.get("poster_title") or "").lower()
    return any(kw in poster_title for kw in _COMMENTARY_KEYWORDS)


def _load_cv() -> str:
    return CV_PATH.read_text(encoding="utf-8")


def _build_job_text(job: dict) -> str:
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
    return "\n".join(parts)


def _build_location_rules(locations: list[str]) -> str:
    if not locations:
        locations = ["London", "UK"]
    accept_remote = any("remote" in loc.lower() for loc in locations)
    loc_list = ", ".join(locations)
    lines = [
        "LOCATION RULES — apply strictly before considering technical fit:",
        f"- Candidate's preferred locations: {loc_list}.",
        "- If the job location matches ANY preferred location: score normally, no location caveat.",
    ]
    if accept_remote:
        lines.append("- Remote jobs: score on technical fit as normal.")
    else:
        lines.append(
            "- Remote jobs with no preferred-location mention: score on technical fit "
            "but note it is remote in the reason."
        )
    lines.append(
        "- If the job location matches NONE of the preferred locations and is not remote: "
        "score 3 or below regardless of technical fit."
    )
    lines.append(
        "- Location unspecified or ambiguous: score on technical fit, "
        "append 'Location unconfirmed — verify before applying' to reason."
    )
    return "\n".join(lines)


def _build_sponsor_rules(visa_status: str) -> tuple[str, str]:
    """Returns (sponsor_rules_text, sponsor_likelihood_format_hint)."""
    if visa_status in _NO_SPONSOR_STATUSES:
        return (
            "VISA / SPONSOR RULES:\n"
            "- Candidate does not require visa sponsorship. Ignore sponsor licence requirements entirely.\n"
            "- Always output Sponsor Likelihood: N/A.",
            "N/A",
        )
    if visa_status in _STRICT_SPONSOR_STATUSES:
        return (
            "VISA / SPONSOR RULES:\n"
            "- Candidate requires employer sponsorship to work in the UK.\n"
            "- Reduce score by up to 2 points for jobs where sponsorship is Unlikely.\n"
            "- Likely = large/established company known to sponsor; "
            "Unlikely = SME or startup with no evidence of sponsoring; Unknown = insufficient info.",
            "Likely|Unlikely|Unknown",
        )
    if visa_status in _SWITCH_SPONSOR_STATUSES:
        return (
            "VISA / SPONSOR RULES:\n"
            "- Candidate is on a Skilled Worker Visa; switching companies requires the new employer to become a new sponsor.\n"
            "- Likely = large/established company known to sponsor; Unlikely = SME or startup; Unknown = insufficient info.",
            "Likely|Unlikely|Unknown",
        )
    # Graduate Route Visa, Youth Mobility Scheme, or unrecognised
    return (
        "VISA / SPONSOR RULES:\n"
        "- Candidate is on a time-limited UK visa (Graduate Route or Youth Mobility Scheme).\n"
        "- Flag sponsor likelihood for future reference — it will matter when this visa expires.\n"
        "- Likely = large/established company known to sponsor; Unlikely = SME or startup; Unknown = insufficient info.",
        "Likely|Unlikely|Unknown",
    )


def _parse_response(text: str) -> dict:
    result = {"score": 0, "reason": "", "sponsor_likelihood": "Unknown"}
    for line in text.strip().splitlines():
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
            if val in _VALID_SPONSOR:
                result["sponsor_likelihood"] = val
    return result


def score_jobs(
    jobs: list[dict],
    locations: list[str] | None = None,
    visa_status: str = "",
) -> list[dict]:
    cv_text = _load_cv()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    location_rules = _build_location_rules(locations or [])
    sponsor_rules, sponsor_hint = _build_sponsor_rules(visa_status)

    system_prompt = (
        "You are a recruitment analyst. You will be given a candidate's CV and a job posting. "
        "Score the fit from 1–10 and assess visa sponsorship likelihood.\n\n"
        "The candidate is targeting roles in: ML Engineer, AI Engineer, NLP Engineer, Applied AI Engineer, "
        "Machine Learning Engineer, LLM Engineer, Data Scientist. Do not penalise Data Scientist roles — "
        "they are an explicit target for this candidate.\n\n"
        f"{location_rules}\n\n"
        f"{sponsor_rules}\n\n"
        f"CANDIDATE CV:\n{cv_text}"
    )

    scored = []
    for job in jobs:
        if _is_commentary_post(job):
            logger.debug("COMMENTARY  score=2  %s @ %s", job.get("title"), job.get("company"))
            continue
        job_text = _build_job_text(job)
        user_prompt = (
            f"{job_text}\n\n"
            "Respond in exactly this format (3 lines only):\n"
            "Score: <1-10>\n"
            "Reason: <2 lines max explaining the fit>\n"
            f"Sponsor Likelihood: <{sponsor_hint}>"
        )

        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=1000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            parsed = _parse_response(message.content[0].text)
            if parsed["score"] >= SCORE_THRESHOLD:
                scored.append({**job, **parsed})
                logger.info("PASS  score=%d  %s @ %s", parsed["score"], job.get("title"), job.get("company"))
            else:
                logger.debug("SKIP  score=%d  %s @ %s", parsed["score"], job.get("title"), job.get("company"))
        except Exception as exc:
            logger.warning("Scoring failed for '%s': %s", job.get("title"), exc)

    return scored
