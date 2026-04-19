import os
import logging
import anthropic
from pathlib import Path

logger = logging.getLogger(__name__)

CV_PATH = Path(__file__).parent.parent / "data" / "my_cv.txt"
SCORE_THRESHOLD = 7
MODEL = "claude-haiku-4-5-20251001"


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
            if val in ("Likely", "Unlikely", "Unknown"):
                result["sponsor_likelihood"] = val
    return result


def score_jobs(jobs: list[dict]) -> list[dict]:
    cv_text = _load_cv()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = (
        "You are a recruitment analyst. You will be given a candidate's CV and a job posting. "
        "Score the fit from 1–10 and assess visa sponsorship likelihood.\n\n"
        "The candidate is targeting roles in: ML Engineer, AI Engineer, NLP Engineer, Applied AI Engineer, "
        "Machine Learning Engineer, LLM Engineer, Data Scientist. Do not penalise Data Scientist roles — "
        "they are an explicit target for this candidate.\n\n"
        "LOCATION RULES — apply these strictly before considering technical fit:\n"
        "- If the job explicitly states a non-UK location and is not remote: score 3 or below regardless of fit.\n"
        "- If the job is Remote with no UK mention: score fairly on technical fit, but set Sponsor Likelihood to "
        "'Unlikely' and note it is remote in the reason.\n"
        "- If location is not specified or is ambiguous: score on technical fit as normal, but include the note "
        "'Location unconfirmed — verify before applying' at the end of the reason.\n"
        "- If location is London or UK: score normally with no location caveat.\n\n"
        f"CANDIDATE CV:\n{cv_text}"
    )

    scored = []
    for job in jobs:
        job_text = _build_job_text(job)
        user_prompt = (
            f"{job_text}\n\n"
            "Respond in exactly this format (3 lines only):\n"
            "Score: <1-10>\n"
            "Reason: <2 lines max explaining the fit>\n"
            "Sponsor Likelihood: <Likely|Unlikely|Unknown>"
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
