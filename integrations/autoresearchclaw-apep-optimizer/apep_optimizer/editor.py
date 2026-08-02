"""Editorial decision aggregation across parallel referee reviews."""

from __future__ import annotations

from pathlib import Path

from . import prompts_apep as P
from .models import DECISION_SEVERITY, Decision, OptimizerConfig, ReviewResult


def aggregate_decision(
    reviews: list[ReviewResult], policy: str = "majority"
) -> Decision:
    """Combine referee decisions into one editorial decision.

    ``majority``: most common decision wins; ties resolve to the worse
    decision (a paper never benefits from referee disagreement).
    ``worst``: the most severe decision wins outright.

    Reviews that failed or whose decision could not be parsed are treated
    as MAJOR REVISION so silent failures never wave a paper through.
    """
    decisions = [
        r.decision if (r.ok and r.decision) else Decision.MAJOR_REVISION
        for r in reviews
    ]
    if not decisions:
        return Decision.MAJOR_REVISION
    if policy == "worst":
        return max(decisions, key=DECISION_SEVERITY.index)
    counts: dict[Decision, int] = {}
    for decision in decisions:
        counts[decision] = counts.get(decision, 0) + 1
    best_count = max(counts.values())
    tied = [d for d, c in counts.items() if c == best_count]
    return max(tied, key=DECISION_SEVERITY.index)


def write_final_review(
    out_dir: Path,
    family_id: str,
    version: int,
    round_idx: int,
    config: OptimizerConfig,
    reviews: list[ReviewResult],
    decision: Decision,
    notes: str = "",
) -> Path:
    """Write ``final_review.md`` summarizing the round, APEP-style."""
    referee_lines = "\n".join(
        f"- **{r.reviewer}** ({r.model}): "
        + (
            r.decision.value
            if (r.ok and r.decision)
            else f"FAILED ({r.error or 'unparseable decision'})"
        )
        for r in reviews
    )
    text = P.FINAL_REVIEW_TEMPLATE.format(
        family_id=family_id,
        version=version,
        round=round_idx,
        max_rounds=config.max_rounds,
        policy=config.aggregation_policy,
        decision=decision.value,
        referee_lines=referee_lines or "- (no referees ran)",
        notes=notes or "None.",
    )
    path = out_dir / "final_review.md"
    path.write_text(text, encoding="utf-8")
    return path
