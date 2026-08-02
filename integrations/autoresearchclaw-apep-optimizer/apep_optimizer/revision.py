"""Revision-plan generation, automated revision, and reply-to-reviewers.

Mirrors the APEP revision artifacts: ``revision_plan.md``, a full revised
paper, and ``reply_to_reviewers_1.md``. Includes the same safety guards
AutoResearchClaw uses in its Stage 19 single-pass revision: an
anti-fabrication data-integrity notice and a length guard that prevents
the model from silently truncating the paper.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from . import prompts_apep as P
from .models import OptimizerConfig, ReviewResult
from .reviewers import _chat

logger = logging.getLogger(__name__)

#: Revised paper must be at least this fraction of the original length.
LENGTH_GUARD_RATIO = 0.8
#: Length guard only applies to papers longer than this many words.
LENGTH_GUARD_MIN_WORDS = 500


def concat_reviews(reviews: list[ReviewResult]) -> str:
    parts = []
    for review in reviews:
        if not review.ok:
            continue
        decision = review.decision.value if review.decision else "UNPARSED"
        parts.append(
            f"## Referee {review.reviewer} ({review.model}) — {decision}\n\n"
            f"{review.review_text}"
        )
    return "\n\n---\n\n".join(parts)


def build_revision_plan(
    llm: Any,
    paper: str,
    reviews: list[ReviewResult],
    config: OptimizerConfig,
    out_dir: Path,
) -> str:
    plan = _chat(
        llm,
        P.REVISION_PLAN_SYSTEM,
        P.REVISION_PLAN_USER.format(reviews=concat_reviews(reviews), paper=paper),
        model="",
        max_tokens=config.review_max_tokens,
        temperature=config.revision_temperature,
    )
    (out_dir / "revision_plan.md").write_text(plan, encoding="utf-8")
    return plan


def revise_paper(
    llm: Any,
    paper: str,
    plan: str,
    reviews: list[ReviewResult],
    config: OptimizerConfig,
    out_dir: Path,
    *,
    fmt: str = "markdown",
) -> str:
    """Produce the complete revised paper, with a length guard.

    If the revision comes back shorter than 80% of the original, retry once
    with an explicit length requirement; if it is still short, keep the
    original paper and archive the short output as internal notes (never
    lose content).
    """
    evidence = ""
    if config.experiment_evidence:
        evidence = (
            "\n# Verified experiment data (DATA INTEGRITY: never add numbers "
            "that are not in this data; state a limitation instead)\n\n"
            f"{config.experiment_evidence}\n"
        )
    user = P.REVISE_USER.format(
        plan=plan,
        reviews=concat_reviews(reviews),
        experiment_evidence=evidence,
        paper=paper,
    )
    system = P.REVISE_SYSTEM.format(fmt=fmt)
    original_words = len(paper.split())
    # ~1.5 tokens/word with 20% headroom, same heuristic as Stage 19
    max_tokens = max(config.revision_max_tokens, int(original_words * 1.5 * 1.2))

    revised = _chat(
        llm, system, user, model="", max_tokens=max_tokens,
        temperature=config.revision_temperature,
    )
    if original_words > LENGTH_GUARD_MIN_WORDS and len(revised.split()) < int(
        original_words * LENGTH_GUARD_RATIO
    ):
        logger.warning(
            "Revision is %d words vs %d in original — retrying with length "
            "enforcement.", len(revised.split()), original_words,
        )
        retry_user = (
            f"CRITICAL LENGTH REQUIREMENT: the paper is {original_words} words; "
            f"your revision MUST be at least {original_words} words. Copy every "
            "section the referees did not criticize UNCHANGED.\n\n" + user
        )
        retry = _chat(
            llm, system, retry_user, model="", max_tokens=max_tokens,
            temperature=config.revision_temperature,
        )
        if len(retry.split()) >= int(original_words * LENGTH_GUARD_RATIO):
            revised = retry
        else:
            longer = retry if len(retry.split()) > len(revised.split()) else revised
            (out_dir / "revision_notes_internal.md").write_text(
                longer, encoding="utf-8"
            )
            logger.warning(
                "Both revision attempts were too short — keeping the original "
                "paper and archiving the model output as internal notes."
            )
            revised = paper
    return revised


def reply_to_reviewers(
    llm: Any,
    plan: str,
    reviews: list[ReviewResult],
    round_idx: int,
    config: OptimizerConfig,
    out_dir: Path,
) -> str:
    reply = _chat(
        llm,
        P.REPLY_SYSTEM,
        P.REPLY_USER.format(
            round=round_idx, reviews=concat_reviews(reviews), plan=plan
        ),
        model="",
        max_tokens=config.review_max_tokens,
        temperature=config.revision_temperature,
    )
    (out_dir / "reply_to_reviewers_1.md").write_text(reply, encoding="utf-8")
    return reply
