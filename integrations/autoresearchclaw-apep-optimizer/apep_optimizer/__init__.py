"""APEP-style automatic paper optimization for AutoResearchClaw.

This package ports the paper-optimization loop used by the APE Papers
project (https://github.com/LamKaGum/ape-papers) into AutoResearchClaw:

    multi-model parallel referee reviews
      -> editor decision (ACCEPT / MINOR REVISION / MAJOR REVISION / ...)
        -> revision plan -> automated revision -> reply to reviewers
          -> new paper version (v2, v3, ...) with parent lineage
            -> re-review, until acceptance or round budget exhausted.

Drop this directory into ``researchclaw/apep_optimizer/`` and register the
CLI command described in ``cli.py`` to expose ``researchclaw optimize``.

The package only requires an LLM client object exposing::

    client.chat(messages, *, model=None, max_tokens=None,
                temperature=None, system=None) -> obj with .content

which matches ``researchclaw.llm.client.LLMClient.chat``.
"""

from .models import (
    AdvisorResult,
    Decision,
    OptimizerConfig,
    ReviewResult,
    parse_decision,
    parse_verdict,
)
from .loop import PaperOptimizer

__all__ = [
    "AdvisorResult",
    "Decision",
    "OptimizerConfig",
    "PaperOptimizer",
    "ReviewResult",
    "parse_decision",
    "parse_verdict",
]

__version__ = "0.1.0"
