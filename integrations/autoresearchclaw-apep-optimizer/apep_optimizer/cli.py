"""CLI wiring for ``researchclaw optimize``.

After copying this package to ``researchclaw/apep_optimizer/``, register the
subcommand in ``researchclaw/cli.py`` (see ``INSTALL.md`` for the two-line
diff)::

    from researchclaw.apep_optimizer.cli import add_optimize_parser
    ...
    add_optimize_parser(sub)   # inside build_parser()

Usage examples::

    # Optimize an existing paper (markdown or LaTeX), 3 rounds max
    researchclaw optimize --paper artifacts/rc-.../deliverables/paper_draft.md

    # Heterogeneous referee panel, APEP-style
    researchclaw optimize --paper paper.tex \\
        --reviewer gpt=gpt-5-mini --reviewer grok=grok-4.1-fast \\
        --reviewer gemini=gemini-3-flash-preview \\
        --advisor code=gpt-5.1-codex-mini --rounds 4
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .loop import PaperOptimizer
from .models import OptimizerConfig


def _parse_model_specs(specs: list[str] | None, default_names: list[str]) -> dict[str, str]:
    """Parse repeated ``--reviewer name=model`` flags into an ordered dict.

    A bare ``--reviewer model-id`` (no ``=``) auto-names the referee
    ``r1``, ``r2``, ... in order.
    """
    if not specs:
        return {name: "" for name in default_names}
    out: dict[str, str] = {}
    for idx, spec in enumerate(specs, start=1):
        if "=" in spec:
            name, model = spec.split("=", 1)
        else:
            name, model = f"r{idx}", spec
        out[name.strip()] = model.strip()
    return out


def add_optimize_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "optimize",
        help="APEP-style iterative paper optimization (multi-model review -> revise loop)",
    )
    p.add_argument("--paper", required=True, help="Path to paper .md or .tex")
    p.add_argument(
        "--out", default="", help="Family output directory (default: <paper>_apep/)"
    )
    p.add_argument("--family-id", default="", help="Paper family id (default: dir name)")
    p.add_argument("--rounds", type=int, default=3, help="Max review->revise rounds")
    p.add_argument(
        "--reviewer",
        action="append",
        metavar="NAME=MODEL",
        help="Referee model, repeatable (default: 3 referees on default model)",
    )
    p.add_argument(
        "--advisor",
        action="append",
        metavar="NAME=MODEL",
        help="Advisor model for the PASS/FAIL fatal-flaw gate, repeatable",
    )
    p.add_argument(
        "--policy",
        choices=("majority", "worst"),
        default="majority",
        help="Decision aggregation policy across referees",
    )
    p.add_argument(
        "--accept",
        default="ACCEPT,CONDITIONALLY ACCEPT",
        help="Comma-separated decisions that stop the loop",
    )
    p.add_argument("--config", default=None, help="ResearchClaw config YAML")
    p.set_defaults(func=cmd_optimize)


def _build_llm(config_path: str | None):
    """Build the ResearchClaw LLMClient from config (or env defaults)."""
    from researchclaw.config import load_config, resolve_config_path
    from researchclaw.llm.client import LLMClient

    resolved = resolve_config_path(config_path)
    if resolved is None:
        print(
            "error: no config file found — run `researchclaw init` or pass --config",
            file=sys.stderr,
        )
        raise SystemExit(2)
    rc_config = load_config(resolved, check_paths=False)
    return LLMClient.from_rc_config(rc_config)


def cmd_optimize(args: argparse.Namespace) -> int:
    from .models import Decision

    paper = Path(args.paper)
    if not paper.exists():
        print(f"error: paper not found: {paper}", file=sys.stderr)
        return 2
    family_dir = Path(args.out) if args.out else paper.parent / f"{paper.stem}_apep"

    accept = frozenset(
        Decision(d.strip()) for d in args.accept.split(",") if d.strip()
    )
    config = OptimizerConfig(
        reviewer_models=_parse_model_specs(args.reviewer, ["r1", "r2", "r3"]),
        advisor_models=_parse_model_specs(args.advisor, []) if args.advisor else {},
        max_rounds=args.rounds,
        aggregation_policy=args.policy,
        accept_decisions=accept,
        advisor_gate_enabled=bool(args.advisor),
    )

    llm = _build_llm(args.config)
    optimizer = PaperOptimizer(llm, config)
    report = optimizer.optimize(
        paper, family_dir, family_id=args.family_id or None
    )

    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    print(
        f"\n{'ACCEPTED' if report.accepted else 'NOT ACCEPTED'} — "
        f"final version v{report.final_version} "
        f"({report.final_decision.value}) at {report.final_paper_path}"
    )
    return 0 if report.accepted else 1
