import os
import logging
import anthropic
from pathlib import Path

logger = logging.getLogger(__name__)

CV_PATH = Path(__file__).parent.parent / "data" / "my_cv.txt"
MODEL = "claude-haiku-4-5-20251001"


def _load_cv() -> str:
    return CV_PATH.read_text(encoding="utf-8")


def _parse_gaps(text: str) -> dict:
    result = {"hard_gaps": [], "soft_gaps": [], "hidden_strengths": []}
    current = None
    for line in text.strip().splitlines():
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
            if item and len(result[current]) < 3:
                result[current].append(item)
    return result


def analyse_gaps(jobs: list[dict]) -> list[dict]:
    cv_text = _load_cv()
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    system_prompt = (
        "You are a career coach performing CV gap analysis. "
        "Compare a job description against the candidate's CV and identify gaps and strengths.\n\n"
        f"CANDIDATE CV:\n{cv_text}"
    )

    results = []
    for job in jobs:
        job_desc = job.get("description") or job.get("post_text") or ""
        user_prompt = (
            f"Job Title: {job.get('title', '')}\n"
            f"Company: {job.get('company', '')}\n"
            f"Description:\n{job_desc}\n\n"
            "Respond in exactly this format. Max 3 bullets per section, one concise line each:\n\n"
            "Hard Gaps:\n- <item>\n\n"
            "Soft Gaps:\n- <item>\n\n"
            "Hidden Strengths:\n- <item>"
        )

        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=300,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            gaps = _parse_gaps(message.content[0].text)
            results.append({**job, "gap_analysis": gaps})
            logger.info("Gap analysis done for '%s' @ %s", job.get("title"), job.get("company"))
        except Exception as exc:
            logger.warning("Gap analysis failed for '%s': %s", job.get("title"), exc)
            results.append({**job, "gap_analysis": {"hard_gaps": [], "soft_gaps": [], "hidden_strengths": []}})

    return results
