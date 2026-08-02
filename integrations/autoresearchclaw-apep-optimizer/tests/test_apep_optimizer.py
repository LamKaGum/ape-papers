"""Offline tests for the APEP optimizer (no network, fake LLM)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apep_optimizer import Decision, OptimizerConfig, PaperOptimizer, parse_decision, parse_verdict
from apep_optimizer.editor import aggregate_decision
from apep_optimizer.models import ReviewResult, load_manifest


@dataclass
class _Resp:
    content: str


class FakeLLM:
    """Scripted LLM: routes by prompt type, decisions scripted per round."""

    def __init__(self, decisions_per_round: list[list[str]], paper_words: int = 100):
        self.decisions_per_round = decisions_per_round
        self.review_call = 0
        self.paper_words = paper_words
        self.calls: list[str] = []

    def chat(self, messages, *, model=None, max_tokens=None, temperature=None,
             system=None, **kwargs):
        user = messages[-1]["content"]
        sysline = (system or "").splitlines()[0] if system else ""
        if "external referee" in sysline:
            self.calls.append("review")
            round_idx = self.review_call // 3
            slot = self.review_call % 3
            self.review_call += 1
            decision = self.decisions_per_round[round_idx][slot]
            return _Resp(f"Detailed critique here.\n\nDECISION: {decision}\n")
        if "senior research advisor" in sysline:
            self.calls.append("advisor")
            return _Resp("No fatal problems found.\n\nVERDICT: PASS\n")
        if "handling editor" in sysline:
            self.calls.append("plan")
            return _Resp("## Context\n...\n## Workstreams\n1. Fix X.\n")
        if "point-by-point reply" in sysline:
            self.calls.append("reply")
            return _Resp("# Reply to Reviewers\n> concern\n**Response:** fixed.\n")
        if "author of the paper" in sysline:
            self.calls.append("revise")
            return _Resp("REVISED. " + "word " * self.paper_words)
        raise AssertionError(f"unexpected prompt: {sysline!r}")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("...\nDECISION: MINOR REVISION", Decision.MINOR_REVISION),
        ("**Decision:** Conditionally Accept", Decision.CONDITIONALLY_ACCEPT),
        ("decision - REJECT AND RESUBMIT", Decision.REJECT_AND_RESUBMIT),
        ("Overall I recommend major revisions.\nDECISION: MAJOR REVISION",
         Decision.MAJOR_REVISION),
        ("Final verdict\n\nDECISION: ACCEPT", Decision.ACCEPT),
        ("no decision anywhere in sight", None),
    ],
)
def test_parse_decision(text, expected):
    assert parse_decision(text) == expected


def test_parse_decision_prefers_explicit_line_over_body():
    text = "The paper could be accepted after fixes.\nDECISION: MAJOR REVISION"
    assert parse_decision(text) == Decision.MAJOR_REVISION


def test_parse_verdict():
    assert parse_verdict("looks fine\nVERDICT: PASS") == "PASS"
    assert parse_verdict("fatal issue\nVERDICT: FAIL") == "FAIL"
    assert parse_verdict("hmm") == "UNCLEAR"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _review(decision: Decision | None, ok: bool = True) -> ReviewResult:
    return ReviewResult(
        reviewer="x", model="m",
        decision=decision,
        review_text="body" if ok else "",
        error="" if ok else "boom",
    )


def test_majority_wins():
    reviews = [_review(Decision.ACCEPT), _review(Decision.ACCEPT),
               _review(Decision.MAJOR_REVISION)]
    assert aggregate_decision(reviews, "majority") == Decision.ACCEPT


def test_majority_tie_resolves_to_worse():
    reviews = [_review(Decision.ACCEPT), _review(Decision.MAJOR_REVISION)]
    assert aggregate_decision(reviews, "majority") == Decision.MAJOR_REVISION


def test_worst_policy():
    reviews = [_review(Decision.ACCEPT), _review(Decision.ACCEPT),
               _review(Decision.REJECT_AND_RESUBMIT)]
    assert aggregate_decision(reviews, "worst") == Decision.REJECT_AND_RESUBMIT


def test_failed_review_counts_as_major_revision():
    reviews = [_review(Decision.ACCEPT), _review(None, ok=False)]
    assert aggregate_decision(reviews, "majority") == Decision.MAJOR_REVISION


# ---------------------------------------------------------------------------
# Full loop
# ---------------------------------------------------------------------------

def _config(**kwargs) -> OptimizerConfig:
    base = dict(
        reviewer_models={"gpt": "", "grok": "", "gemini": ""},
        advisor_models={"code": ""},
        max_rounds=3,
        advisor_gate_enabled=True,
    )
    base.update(kwargs)
    return OptimizerConfig(**base)


def test_loop_converges_to_accept(tmp_path: Path):
    paper = tmp_path / "paper.md"
    paper.write_text("# Title\n\n" + "word " * 100, encoding="utf-8")
    llm = FakeLLM([
        ["MAJOR REVISION", "MAJOR REVISION", "MINOR REVISION"],  # v1
        ["ACCEPT", "ACCEPT", "MINOR REVISION"],                  # v2
    ])
    report = PaperOptimizer(llm, _config()).optimize(paper, tmp_path / "fam")

    assert report.accepted
    assert report.final_version == 2
    assert report.final_decision == Decision.ACCEPT
    assert report.final_paper_path.read_text(encoding="utf-8").startswith("REVISED.")

    fam = tmp_path / "fam"
    # v1 artifacts: reviews, advisor, final review, summary
    assert (fam / "v1" / "review_gpt_1.md").exists()
    assert (fam / "v1" / "advisor_code_1.md").exists()
    assert (fam / "v1" / "final_review.md").exists()
    assert (fam / "v1" / "parallel_review_summary.json").exists()
    # v2 artifacts: revision plan + reply + new reviews
    assert (fam / "v2" / "revision_plan.md").exists()
    assert (fam / "v2" / "reply_to_reviewers_1.md").exists()
    assert (fam / "v2" / "review_grok_1.md").exists()
    assert (fam / "optimization_summary.json").exists()

    # Manifest lineage matches APEP schema
    manifest = load_manifest(fam)
    ids = {p["paper_id"]: p for p in manifest["papers"]}
    assert ids["fam_v1"]["decision"] == "MAJOR REVISION"
    assert ids["fam_v2"]["decision"] == "ACCEPT"
    assert ids["fam_v2"]["is_revision"] is True
    assert ids["fam_v2"]["parent_paper_id"] == "fam_v1"
    assert ids["fam_v2"]["paper_family_id"] == "fam"


def test_loop_stops_at_round_budget(tmp_path: Path):
    paper = tmp_path / "paper.md"
    paper.write_text("# Title\n\n" + "word " * 100, encoding="utf-8")
    llm = FakeLLM([["MAJOR REVISION"] * 3] * 3)
    report = PaperOptimizer(llm, _config(max_rounds=2)).optimize(
        paper, tmp_path / "fam"
    )
    assert not report.accepted
    assert report.final_version == 2
    assert len(report.rounds) == 2
    # No v3 was produced: budget exhausted after reviewing v2
    assert not (tmp_path / "fam" / "v3").exists()


def test_first_round_accept_no_revision(tmp_path: Path):
    paper = tmp_path / "paper.md"
    paper.write_text("# Title\n\n" + "word " * 100, encoding="utf-8")
    llm = FakeLLM([["ACCEPT", "ACCEPT", "ACCEPT"]])
    report = PaperOptimizer(llm, _config()).optimize(paper, tmp_path / "fam")
    assert report.accepted and report.final_version == 1
    assert "revise" not in llm.calls


def test_length_guard_keeps_original_when_revision_truncates(tmp_path: Path):
    paper = tmp_path / "paper.md"
    original = "# Title\n\n" + "word " * 1000
    paper.write_text(original, encoding="utf-8")
    # Revised output is only ~10 words (far below 80% of 1000)
    llm = FakeLLM(
        [["MAJOR REVISION"] * 3, ["ACCEPT"] * 3],
        paper_words=10,
    )
    report = PaperOptimizer(llm, _config()).optimize(paper, tmp_path / "fam")
    final_text = report.final_paper_path.read_text(encoding="utf-8")
    assert final_text == original  # original preserved, no content loss
    assert (tmp_path / "fam" / "v2" / "revision_notes_internal.md").exists()


def test_optimization_summary_is_valid_json(tmp_path: Path):
    paper = tmp_path / "paper.md"
    paper.write_text("# T\n\n" + "word " * 50, encoding="utf-8")
    llm = FakeLLM([["ACCEPT"] * 3])
    PaperOptimizer(llm, _config()).optimize(paper, tmp_path / "fam")
    data = json.loads(
        (tmp_path / "fam" / "optimization_summary.json").read_text(encoding="utf-8")
    )
    assert data["accepted"] is True
    assert data["rounds"][0]["referee_decisions"]["gpt"] == "ACCEPT"
