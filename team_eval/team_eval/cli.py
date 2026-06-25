"""CLI: parse a FleetView team session → graph → ATIF → eval → interactive HTML.

Usage:
    python -m team_eval.cli <session-jsonl-or-dir> [--out runs/] [--checks-only]
"""

from __future__ import annotations

import argparse
import sys

from team_eval.harness.pipeline import process_session

# Scorecard uses Unicode glyphs (✓✗!?). On Windows the default stdout codec
# is often GBK, which can't encode them — force UTF-8 so the CLI prints cleanly
# regardless of console code page.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (ValueError, OSError):
        pass

_SYM = {"pass": "✓", "fail": "✗", "warn": "!", "not_applicable": "–", "pending": "?"}


def _print_scorecard(report) -> None:
    sc = report.score.get("counts", {})
    print("\n" + "=" * 78)
    print(f"  TEAM EVAL · {report.team_name or report.session_id}  "
          f"(pattern={report.pattern}, reduce={report.reduce_quality})")
    print("=" * 78)
    print(f"  health={report.score.get('health')}  "
          f"pass={sc.get('pass',0)} warn={sc.get('warn',0)} "
          f"fail={sc.get('fail',0)} pending={sc.get('pending',0)} "
          f"na={sc.get('not_applicable',0)}")
    print("-" * 78)
    cur = None
    for c in report.checks:
        if c.dimension != cur:
            cur = c.dimension
            print(f"\n  [{cur}]")
        sym = _SYM.get(c.outcome.value if c.outcome else "pending", "?")
        out = (c.outcome.value if c.outcome else "pending").ljust(14)
        print(f"   {sym} {c.id:<4} {c.title[:40]:<40} {out} {c.explanation or ''}")
    print("\n" + "=" * 78)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="team_eval", description=__doc__)
    parser.add_argument("session", help="session .jsonl file or its directory")
    parser.add_argument("--out", default="runs", help="output directory (default: runs)")
    parser.add_argument("--sessions", default=None,
                        help="dataset dir; if set, register the session into its index")
    parser.add_argument("--checks-only", action="store_true",
                        help="only print the scorecard, write no artifacts")
    parser.add_argument("--register", action="store_true",
                        help="register the session into --sessions dataset index")
    parser.add_argument("--judge", action="store_true",
                        help="run the cc-sdk LLM judge on subjective checks (spends tokens)")
    parser.add_argument("--judge-cwd", default=".",
                        help="cwd for the judge (the repo under review), default .")
    parser.add_argument("--judge-model", default=None, help="model override for the judge")
    args = parser.parse_args(argv)

    result = process_session(
        args.session,
        runs_dir=args.out,
        sessions_dir=args.sessions,
        register=args.register and args.sessions is not None,
        judge=args.judge,
        judge_workdir=args.judge_cwd,
        judge_model=args.judge_model,
        write=not args.checks_only,
    )
    report = result["report"]

    _print_scorecard(report)

    if args.checks_only:
        return 0

    out_dir = result["out_dir"]
    html_path = result["html_path"]
    graph = result["graph"]
    teammate_roles = [n for n in graph.nodes if n.kind == "teammate"]
    transcripts = sum(n.transcript_count for n in teammate_roles)
    print(f"\n  artifacts → {out_dir}")
    print(f"    atif.json   ({transcripts} embedded subagent trajectories across "
          f"{len(teammate_roles)} roles)")
    print(f"    graph.json  ({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
    print(f"    eval.json")
    print(f"    report.html → open: file:///{str(html_path.resolve()).replace(chr(92), '/')}")
    print(f"\n  ATIF valid: {result['atif_valid']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
