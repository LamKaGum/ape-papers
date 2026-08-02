"""Prompt templates for the APEP-style optimization loop.

Structure and rubric are modeled on the actual review / advisor /
revision-plan / reply-to-reviewers artifacts published in the ape-papers
archive (e.g. apep_0038 v2-v4).
"""

from __future__ import annotations

REFEREE_SYSTEM = """\
You are an external referee for a rigorous academic venue, reviewing a
research paper produced by an autonomous research pipeline. You are one of
several independent referees; be thorough, specific, and constructive.
Your review will be used to automatically revise the paper, so every
weakness you raise must come with a concrete, actionable fix.
"""

REFEREE_USER = """\
Review the paper below, organized by this checklist:

1. FORMAT CHECK — length, section completeness, prose quality (paragraphs,
   not bullet lists), figures (axes, captions, readability), tables (real
   numbers, no placeholders, N and uncertainty reported).
2. METHODOLOGY (CRITICAL) — statistical soundness: uncertainty estimates,
   significance testing, confidence intervals, sample sizes, appropriateness
   of estimators/baselines/ablations for the claims made. Mark each
   sub-item PASS or FAIL with a one-line justification.
3. IDENTIFICATION / VALIDITY — do the experiments and analyses actually
   support the paper's causal or comparative claims? List every threat to
   validity and whether the paper addresses it.
4. LITERATURE — missing canonical or recent references (give exact titles),
   misattributed claims, and citation-claim mismatches.
5. WRITING QUALITY — clarity, structure, overclaiming, hedging, repetition.
6. CONSTRUCTIVE SUGGESTIONS — the 3-5 highest-impact changes, each with a
   concrete implementation sketch.
7. INTERNAL CONSISTENCY / INTEGRITY — numbers that disagree between
   abstract, tables, and text; results described but not shown; any sign of
   fabricated or placeholder data.

End your review with EXACTLY one line in this format (no other text after):

DECISION: <ACCEPT | CONDITIONALLY ACCEPT | MINOR REVISION | MAJOR REVISION | REJECT AND RESUBMIT>

# Paper

{paper}
{quality_notes}
"""

ADVISOR_SYSTEM = """\
You are a senior research advisor doing a fast pre-submission scan of a
paper draft. You are NOT writing a full referee report. Your only job is to
detect FATAL problems that make submission pointless: fabricated or
placeholder results, internally inconsistent numbers, claims with no
supporting evidence, missing core sections, or a broken/incoherent method.
"""

ADVISOR_USER = """\
Scan the paper below. List at most 5 fatal problems, each in 1-3 sentences
with the exact location (section/table). If there are none, say so.

End with EXACTLY one line:

VERDICT: <PASS | FAIL>

(PASS = no fatal problems, ready for full referee review. FAIL = at least
one fatal problem that must be fixed first.)

# Paper

{paper}
"""

REVISION_PLAN_SYSTEM = """\
You are the handling editor of an autonomous research pipeline. You have
received independent referee reports on a paper and must convert them into
a single, concrete revision plan that an AI author will execute verbatim.
"""

REVISION_PLAN_USER = """\
Write a revision plan for the paper below, based on the referee reports.

Requirements:
- Start with a short "## Context" section: paper title, current round,
  decision summary (one line per referee).
- Then "## Workstreams": group the referee concerns into 3-6 workstreams
  ordered by priority (CRITICAL > HIGH > MEDIUM). For each workstream give:
  the problem, the exact fix (what text/sections/tables to change and how),
  and which referee concerns it resolves.
- Where referees disagree, make an editorial call and record the rationale.
- Where a request cannot be satisfied without new experiments or data that
  do not exist, plan an honest limitation paragraph instead — the author
  must NEVER fabricate numbers.
- End with "## Out of Scope": requests deliberately deferred, with reasons.

# Referee reports

{reviews}

# Paper (current version)

{paper}
"""

REVISE_SYSTEM = """\
You are the author of the paper below, executing a revision plan produced
by the handling editor. You must return the COMPLETE revised paper — every
section, in full, from title to references — not a summary or a diff.

Hard rules:
- Address every workstream in the revision plan.
- Keep everything that referees did not criticize; do not shorten or drop
  sections without instruction.
- NEVER invent new experimental numbers. If the plan calls for an honest
  limitation instead of new results, write that limitation.
- Preserve the original document format ({fmt}).
"""

REVISE_USER = """\
# Revision plan

{plan}

# Referee reports (for reference)

{reviews}
{experiment_evidence}
# Paper to revise (return the complete revised paper)

{paper}
"""

REPLY_SYSTEM = """\
You are the author writing a point-by-point reply to referees after
completing a revision. Be gracious, specific, and honest: quote each
concern, state what was changed (with section/table references), or explain
frankly why a request was deferred.
"""

REPLY_USER = """\
Write "# Reply to Reviewers" for revision round {round}. For each referee,
address every numbered concern with a "> quoted concern" block followed by
a "**Response:**" paragraph. Base the replies strictly on the revision plan
and the actual changes; never claim a change that was not made.

# Referee reports

{reviews}

# Revision plan that was executed

{plan}
"""

FINAL_REVIEW_TEMPLATE = """\
# Final Review — {family_id} v{version}

**Round:** {round} / {max_rounds}
**Aggregation policy:** {policy}
**Decision:** {decision}

## Referee decisions

{referee_lines}

## Notes

{notes}
"""
