"""Data model for the APEP-style paper optimization loop.

Mirrors the artifact schema observed in the ape-papers archive
(manifest schema v3): five editorial decisions, PASS/FAIL advisor
verdicts, and per-version lineage records (``paper_family_id``,
``parent_paper_id``, ``is_revision``, ``version``).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class Decision(str, Enum):
    """Editorial decisions used by APEP reviews (manifest ``_decisions``)."""

    ACCEPT = "ACCEPT"
    CONDITIONALLY_ACCEPT = "CONDITIONALLY ACCEPT"
    MINOR_REVISION = "MINOR REVISION"
    MAJOR_REVISION = "MAJOR REVISION"
    REJECT_AND_RESUBMIT = "REJECT AND RESUBMIT"


#: Ordered from best to worst — used by the "worst-case" aggregation policy.
DECISION_SEVERITY: tuple[Decision, ...] = (
    Decision.ACCEPT,
    Decision.CONDITIONALLY_ACCEPT,
    Decision.MINOR_REVISION,
    Decision.MAJOR_REVISION,
    Decision.REJECT_AND_RESUBMIT,
)

#: Decisions that terminate the optimization loop successfully.
DEFAULT_ACCEPT_DECISIONS: frozenset[Decision] = frozenset(
    {Decision.ACCEPT, Decision.CONDITIONALLY_ACCEPT}
)

# Match longest phrases first so "CONDITIONALLY ACCEPT" is not read as
# "ACCEPT" and "REJECT AND RESUBMIT" is not read as a bare reject.
_DECISION_PATTERNS: tuple[tuple[re.Pattern[str], Decision], ...] = (
    (re.compile(r"REJECT\s+AND\s+RESUBMIT", re.I), Decision.REJECT_AND_RESUBMIT),
    (re.compile(r"CONDITIONAL(?:LY)?\s+ACCEPT", re.I), Decision.CONDITIONALLY_ACCEPT),
    (re.compile(r"MINOR\s+REVISIONS?", re.I), Decision.MINOR_REVISION),
    (re.compile(r"MAJOR\s+REVISIONS?", re.I), Decision.MAJOR_REVISION),
    (re.compile(r"\bREJECT\b", re.I), Decision.REJECT_AND_RESUBMIT),
    (re.compile(r"\bACCEPT\b", re.I), Decision.ACCEPT),
)

_DECISION_LINE = re.compile(r"^\s*\**\s*DECISION\s*\**\s*[:\-]\s*(.+)$", re.I | re.M)


def parse_decision(text: str) -> Decision | None:
    """Extract the editorial decision from a referee review.

    Prefers an explicit ``DECISION: ...`` line (as required by the review
    prompt); falls back to scanning the last 40 lines of the review.
    """
    if not text:
        return None
    match = _DECISION_LINE.search(text)
    candidates = [match.group(1)] if match else []
    candidates.append("\n".join(text.strip().splitlines()[-40:]))
    for candidate in candidates:
        for pattern, decision in _DECISION_PATTERNS:
            if pattern.search(candidate):
                return decision
    return None


_VERDICT_LINE = re.compile(r"^\s*\**\s*VERDICT\s*\**\s*[:\-]\s*(.+)$", re.I | re.M)


def parse_verdict(text: str) -> str:
    """Extract PASS/FAIL from an advisor response (default: UNCLEAR)."""
    if not text:
        return "UNCLEAR"
    match = _VERDICT_LINE.search(text)
    scope = match.group(1) if match else "\n".join(text.strip().splitlines()[-20:])
    if re.search(r"\bFAIL\b", scope, re.I):
        return "FAIL"
    if re.search(r"\bPASS\b", scope, re.I):
        return "PASS"
    return "UNCLEAR"


@dataclass
class ReviewResult:
    """One referee review from one model."""

    reviewer: str
    model: str
    decision: Decision | None
    review_text: str
    elapsed_seconds: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.review_text.strip())


@dataclass
class AdvisorResult:
    """One quick PASS/FAIL fatal-flaw scan from one advisor model."""

    advisor: str
    model: str
    verdict: str  # PASS | FAIL | UNCLEAR
    text: str
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass
class OptimizerConfig:
    """Tunables for the optimization loop.

    ``reviewer_models`` / ``advisor_models`` map a short reviewer name (used
    in artifact filenames, e.g. ``review_gpt_1.md``) to a model identifier
    understood by the LLM client. An empty model string means "use the
    client's default model chain".
    """

    reviewer_models: dict[str, str] = field(
        default_factory=lambda: {"r1": "", "r2": "", "r3": ""}
    )
    advisor_models: dict[str, str] = field(default_factory=dict)
    max_rounds: int = 3
    accept_decisions: frozenset[Decision] = DEFAULT_ACCEPT_DECISIONS
    aggregation_policy: str = "majority"  # "majority" | "worst"
    advisor_gate_enabled: bool = True
    min_successful_reviews: int = 1
    review_temperature: float = 0.3
    revision_temperature: float = 0.4
    review_max_tokens: int = 8000
    revision_max_tokens: int = 32000
    max_workers: int = 4
    # Anti-fabrication guard text appended to revision prompts when real
    # experiment metrics are available (mirrors Stage 19 in AutoResearchClaw).
    experiment_evidence: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OptimizerConfig":
        kwargs: dict[str, Any] = {}
        simple_fields = (
            "max_rounds",
            "aggregation_policy",
            "advisor_gate_enabled",
            "min_successful_reviews",
            "review_temperature",
            "revision_temperature",
            "review_max_tokens",
            "revision_max_tokens",
            "max_workers",
            "experiment_evidence",
        )
        for name in simple_fields:
            if name in data:
                kwargs[name] = data[name]
        if "reviewer_models" in data:
            kwargs["reviewer_models"] = dict(data["reviewer_models"])
        if "advisor_models" in data:
            kwargs["advisor_models"] = dict(data["advisor_models"])
        if "accept_decisions" in data:
            kwargs["accept_decisions"] = frozenset(
                Decision(d) for d in data["accept_decisions"]
            )
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Version manifest (APEP manifest schema v3, per paper family)
# ---------------------------------------------------------------------------

MANIFEST_NAME = "manifest.json"


def load_manifest(family_dir: Path) -> dict[str, Any]:
    path = family_dir / MANIFEST_NAME
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "_schema_version": 3,
        "_description": (
            "APEP-style optimization manifest — tracks decisions and "
            "lineage for every version of this paper family"
        ),
        "_decisions": [d.value for d in DECISION_SEVERITY],
        "papers": [],
    }


def save_manifest(family_dir: Path, manifest: dict[str, Any]) -> None:
    path = family_dir / MANIFEST_NAME
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def record_version(
    family_dir: Path,
    family_id: str,
    version: int,
    decision: str,
    *,
    date: str,
    paper_relpath: str,
) -> None:
    """Append or update one version record with APEP lineage fields."""
    manifest = load_manifest(family_dir)
    paper_id = f"{family_id}_v{version}"
    record: dict[str, Any] = {
        "paper_id": paper_id,
        "paper_family_id": family_id,
        "decision": decision,
        "date": date,
        "pdf": paper_relpath,
        "version": version,
    }
    if version > 1:
        record["is_revision"] = True
        record["parent_paper_id"] = f"{family_id}_v{version - 1}"
        record["revision_type"] = "reviewer_response"
    papers = [p for p in manifest["papers"] if p.get("paper_id") != paper_id]
    papers.append(record)
    papers.sort(key=lambda p: p.get("version", 0))
    manifest["papers"] = papers
    save_manifest(family_dir, manifest)
