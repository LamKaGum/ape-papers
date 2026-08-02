"""Multi-model parallel referee reviews and advisor gate.

Produces the same artifact shapes as the ape-papers archive:
``review_<name>_1.md`` + ``parallel_review_summary.json`` for referees,
``advisor_<name>_1.md`` + ``advisor_summary.json`` for advisors.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from . import prompts_apep as P
from .models import (
    AdvisorResult,
    OptimizerConfig,
    ReviewResult,
    parse_decision,
    parse_verdict,
)

logger = logging.getLogger(__name__)


def _chat(
    llm: Any,
    system: str,
    user: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
) -> str:
    """Call an LLMClient-compatible object and return the text content."""
    kwargs: dict[str, Any] = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
    }
    if model:
        kwargs["model"] = model
    resp = llm.chat([{"role": "user", "content": user}], **kwargs)
    return getattr(resp, "content", str(resp))


def run_parallel_reviews(
    llm: Any,
    paper: str,
    config: OptimizerConfig,
    out_dir: Path,
    *,
    quality_notes: str = "",
) -> list[ReviewResult]:
    """Run all configured referees concurrently against *paper*.

    Writes ``review_<name>_1.md`` per referee and a
    ``parallel_review_summary.json`` matching the APEP schema.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = f"\n\nAUTOMATED QUALITY ISSUES (flag these):\n{quality_notes}" if quality_notes else ""
    user = P.REFEREE_USER.format(paper=paper, quality_notes=notes)

    started = time.time()
    results: list[ReviewResult] = []

    def _one(name: str, model: str) -> ReviewResult:
        t0 = time.time()
        try:
            text = _chat(
                llm,
                P.REFEREE_SYSTEM,
                user,
                model=model,
                max_tokens=config.review_max_tokens,
                temperature=config.review_temperature,
            )
            return ReviewResult(
                reviewer=name,
                model=model or "default",
                decision=parse_decision(text),
                review_text=text,
                elapsed_seconds=time.time() - t0,
            )
        except Exception as exc:  # noqa: BLE001 — one referee must not kill the round
            logger.warning("Referee %s (%s) failed: %s", name, model, exc)
            return ReviewResult(
                reviewer=name,
                model=model or "default",
                decision=None,
                review_text="",
                elapsed_seconds=time.time() - t0,
                error=str(exc),
            )

    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        futures = {
            pool.submit(_one, name, model): name
            for name, model in config.reviewer_models.items()
        }
        for future in as_completed(futures):
            results.append(future.result())

    # Stable artifact order regardless of completion order
    results.sort(key=lambda r: list(config.reviewer_models).index(r.reviewer))

    files: list[str] = []
    for review in results:
        if not review.ok:
            continue
        path = out_dir / f"review_{review.reviewer}_1.md"
        header = (
            f"# Referee Review — {review.reviewer}\n\n"
            f"**Model:** {review.model}\n"
            f"**Decision:** {review.decision.value if review.decision else 'UNPARSED'}\n"
            f"**Elapsed:** {review.elapsed_seconds:.1f}s\n\n---\n\n"
        )
        path.write_text(header + review.review_text, encoding="utf-8")
        files.append(str(path))

    summary = {
        "mode": "referee",
        "n_reviewers": len(config.reviewer_models),
        "models": [m or "default" for m in config.reviewer_models.values()],
        "successful": sum(1 for r in results if r.ok),
        "failed": sum(1 for r in results if not r.ok),
        "elapsed_seconds": time.time() - started,
        "decisions": [
            {
                "reviewer": r.reviewer,
                "model": r.model,
                "decision": r.decision.value if r.decision else None,
            }
            for r in results
            if r.ok
        ],
        "errors": [
            {"reviewer": r.reviewer, "error": r.error} for r in results if r.error
        ],
        "files": files,
    }
    (out_dir / "parallel_review_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return results


def run_advisor_gate(
    llm: Any,
    paper: str,
    config: OptimizerConfig,
    out_dir: Path,
) -> list[AdvisorResult]:
    """Run the quick PASS/FAIL fatal-flaw scan across advisor models.

    Writes ``advisor_<name>_1.md`` per advisor plus ``advisor_summary.json``.
    Returns an empty list when no advisors are configured.
    """
    if not config.advisor_models:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    user = P.ADVISOR_USER.format(paper=paper)
    started = time.time()
    results: list[AdvisorResult] = []

    def _one(name: str, model: str) -> AdvisorResult:
        t0 = time.time()
        try:
            text = _chat(
                llm,
                P.ADVISOR_SYSTEM,
                user,
                model=model,
                max_tokens=min(config.review_max_tokens, 4000),
                temperature=config.review_temperature,
            )
            return AdvisorResult(
                advisor=name,
                model=model or "default",
                verdict=parse_verdict(text),
                text=text,
                elapsed_seconds=time.time() - t0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Advisor %s (%s) failed: %s", name, model, exc)
            return AdvisorResult(
                advisor=name,
                model=model or "default",
                verdict="UNCLEAR",
                text="",
                elapsed_seconds=time.time() - t0,
                error=str(exc),
            )

    with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
        futures = {
            pool.submit(_one, name, model): name
            for name, model in config.advisor_models.items()
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda r: list(config.advisor_models).index(r.advisor))

    files: list[str] = []
    for advisor in results:
        if advisor.error:
            continue
        path = out_dir / f"advisor_{advisor.advisor}_1.md"
        header = (
            f"# Advisor Scan — {advisor.advisor}\n\n"
            f"**Model:** {advisor.model}\n"
            f"**Verdict:** {advisor.verdict}\n\n---\n\n"
        )
        path.write_text(header + advisor.text, encoding="utf-8")
        files.append(str(path))

    verdicts = [r.verdict for r in results if not r.error]
    summary = {
        "mode": "advisor",
        "n_advisors": len(config.advisor_models),
        "models": [m or "default" for m in config.advisor_models.values()],
        "successful": len(verdicts),
        "failed_api": sum(1 for r in results if r.error),
        "elapsed_seconds": time.time() - started,
        "verdicts": [
            {"advisor": r.advisor, "model": r.model, "verdict": r.verdict}
            for r in results
            if not r.error
        ],
        "pass_count": verdicts.count("PASS"),
        "fail_count": verdicts.count("FAIL"),
        "unclear_count": verdicts.count("UNCLEAR"),
        "overall_pass": bool(verdicts) and verdicts.count("FAIL") == 0,
        "api_errors": [
            {"advisor": r.advisor, "error": r.error} for r in results if r.error
        ],
        "files": files,
    }
    (out_dir / "advisor_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return results
