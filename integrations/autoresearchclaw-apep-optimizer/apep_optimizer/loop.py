"""The APEP-style optimization loop orchestrator.

Given a paper (markdown or LaTeX), iterates:

    v1 --advisor gate--> parallel referee reviews --> editor decision
       |                                                   |
       |            accepted? -> stop                      |
       +<-- new version <- reply <- revise <- revision plan+

Every version lives in its own ``v<N>/`` directory inside the family
directory, exactly like the ape-papers archive, and lineage is recorded in
``manifest.json`` (APEP manifest schema v3 fields: ``paper_family_id``,
``parent_paper_id``, ``is_revision``, ``version``, ``decision``).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .editor import aggregate_decision, write_final_review
from .models import Decision, OptimizerConfig, record_version
from .reviewers import run_advisor_gate, run_parallel_reviews
from .revision import build_revision_plan, revise_paper, reply_to_reviewers

logger = logging.getLogger(__name__)


@dataclass
class RoundOutcome:
    version: int
    decision: Decision
    advisor_overall_pass: bool | None
    referee_decisions: dict[str, str]


@dataclass
class OptimizationReport:
    family_id: str
    family_dir: Path
    accepted: bool
    final_version: int
    final_decision: Decision
    final_paper_path: Path
    rounds: list[RoundOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "family_dir": str(self.family_dir),
            "accepted": self.accepted,
            "final_version": self.final_version,
            "final_decision": self.final_decision.value,
            "final_paper": str(self.final_paper_path),
            "rounds": [
                {
                    "version": r.version,
                    "decision": r.decision.value,
                    "advisor_overall_pass": r.advisor_overall_pass,
                    "referee_decisions": r.referee_decisions,
                }
                for r in self.rounds
            ],
        }


class PaperOptimizer:
    """Runs the APEP optimization loop for one paper family.

    Parameters
    ----------
    llm
        Any object with a ``chat(messages, *, model=None, max_tokens=None,
        temperature=None, system=None)`` method returning an object with a
        ``.content`` attribute — ``researchclaw.llm.client.LLMClient`` fits.
    config
        Loop tunables; see :class:`~apep_optimizer.models.OptimizerConfig`.
    """

    def __init__(self, llm: Any, config: OptimizerConfig | None = None) -> None:
        self.llm = llm
        self.config = config or OptimizerConfig()

    def optimize(
        self,
        paper_path: Path,
        family_dir: Path,
        *,
        family_id: str | None = None,
        quality_notes: str = "",
    ) -> OptimizationReport:
        """Optimize *paper_path*, writing all versions under *family_dir*."""
        paper_path = Path(paper_path)
        family_dir = Path(family_dir)
        family_dir.mkdir(parents=True, exist_ok=True)
        family_id = family_id or family_dir.name
        fmt = "latex" if paper_path.suffix.lower() == ".tex" else "markdown"
        paper_name = f"paper{paper_path.suffix.lower() or '.md'}"

        # Seed v1 with the input paper (idempotent: keep existing v1).
        version = 1
        version_dir = family_dir / "v1"
        version_dir.mkdir(exist_ok=True)
        seeded = version_dir / paper_name
        if not seeded.exists():
            shutil.copyfile(paper_path, seeded)
        paper_text = seeded.read_text(encoding="utf-8")

        rounds: list[RoundOutcome] = []
        decision = Decision.MAJOR_REVISION

        for round_idx in range(1, self.config.max_rounds + 1):
            logger.info(
                "[%s] Round %d/%d — reviewing v%d",
                family_id, round_idx, self.config.max_rounds, version,
            )

            advisor_pass: bool | None = None
            if self.config.advisor_gate_enabled and self.config.advisor_models:
                advisors = run_advisor_gate(
                    self.llm, paper_text, self.config, version_dir
                )
                verdicts = [a.verdict for a in advisors if not a.error]
                advisor_pass = bool(verdicts) and verdicts.count("FAIL") == 0

            reviews = run_parallel_reviews(
                self.llm,
                paper_text,
                self.config,
                version_dir,
                quality_notes=quality_notes,
            )
            successful = [r for r in reviews if r.ok]
            if len(successful) < self.config.min_successful_reviews:
                logger.error(
                    "[%s] Only %d/%d referees succeeded (< min %d) — stopping.",
                    family_id, len(successful), len(reviews),
                    self.config.min_successful_reviews,
                )
                decision = Decision.MAJOR_REVISION
                write_final_review(
                    version_dir, family_id, version, round_idx, self.config,
                    reviews, decision,
                    notes="Round aborted: too few successful referee reviews.",
                )
                rounds.append(RoundOutcome(version, decision, advisor_pass, {}))
                break

            decision = aggregate_decision(
                successful, self.config.aggregation_policy
            )
            # An advisor FAIL means a fatal integrity problem: never accept.
            notes = ""
            if advisor_pass is False and decision in self.config.accept_decisions:
                notes = (
                    "Advisor gate reported a fatal problem — decision demoted "
                    f"from {decision.value} to MAJOR REVISION."
                )
                decision = Decision.MAJOR_REVISION

            write_final_review(
                version_dir, family_id, version, round_idx, self.config,
                reviews, decision, notes=notes,
            )
            record_version(
                family_dir, family_id, version, decision.value,
                date=date.today().isoformat(),
                paper_relpath=f"v{version}/{paper_name}",
            )
            rounds.append(
                RoundOutcome(
                    version,
                    decision,
                    advisor_pass,
                    {
                        r.reviewer: (r.decision.value if r.decision else "UNPARSED")
                        for r in successful
                    },
                )
            )

            if decision in self.config.accept_decisions:
                logger.info("[%s] v%d accepted (%s).", family_id, version, decision.value)
                break
            if round_idx == self.config.max_rounds:
                logger.info(
                    "[%s] Round budget exhausted at v%d (%s).",
                    family_id, version, decision.value,
                )
                break

            # ---- Revise: produce v(N+1) ----------------------------------
            next_dir = family_dir / f"v{version + 1}"
            next_dir.mkdir(exist_ok=True)
            plan = build_revision_plan(
                self.llm, paper_text, successful, self.config, next_dir
            )
            revised = revise_paper(
                self.llm, paper_text, plan, successful, self.config, next_dir,
                fmt=fmt,
            )
            reply_to_reviewers(
                self.llm, plan, successful, round_idx, self.config, next_dir
            )
            (next_dir / paper_name).write_text(revised, encoding="utf-8")
            version += 1
            version_dir = next_dir
            paper_text = revised

        final_paper = family_dir / f"v{version}" / paper_name
        report = OptimizationReport(
            family_id=family_id,
            family_dir=family_dir,
            accepted=decision in self.config.accept_decisions,
            final_version=version,
            final_decision=decision,
            final_paper_path=final_paper,
            rounds=rounds,
        )
        (family_dir / "optimization_summary.json").write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return report
