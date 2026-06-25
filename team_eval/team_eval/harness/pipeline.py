"""End-to-end pipeline: parse → graph → ATIF → eval → viz, shared by CLI & harness."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from team_eval.atif import convert_to_atif, validate_atif
from team_eval.eval import build_eval_report
from team_eval.eval.annotator import load_annotations
from team_eval.graph import build_team_graph
from team_eval.parse import load_team_session
from team_eval.viz import write_report


def process_session(
    session_path: str | Path,
    *,
    runs_dir: str | Path = "runs",
    sessions_dir: str | Path | None = None,
    register: bool = False,
    captured_at: str | None = None,
    judge: bool = False,
    judge_workdir: str = ".",
    judge_model: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Run the full pipeline on one session and write artifacts.

    Returns a dict with the report, graph, atif validation, and output paths.
    If ``register`` is True, append a dataset entry under ``sessions_dir``.
    If ``judge`` is True, run the cc-sdk LLM judge on subjective checks
    (needs claude-agent-sdk + Claude auth; spends tokens).
    If ``write`` is False, skip all artifact writes (used by ``--checks-only``);
    the report/graph are still returned so the caller can print the scorecard
    without clobbering any previously-written (e.g. judged) artifacts.
    """
    session = load_team_session(session_path)
    graph = build_team_graph(session)
    traj = convert_to_atif(session, graph)
    atif_val = validate_atif(traj)
    generated_at = captured_at or datetime.now(timezone.utc).isoformat()

    annotations = load_annotations(runs_dir, session.session_id)
    report = build_eval_report(
        session, graph, atif_val, annotations=annotations, generated_at=generated_at
    )

    if judge:
        from team_eval.eval.llm_judge import judge_report

        report = judge_report(report, session, graph,
                              workdir=judge_workdir, model=judge_model)

    out_dir = Path(runs_dir) / session.session_id
    html_path = None
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "atif.json").write_text(
            json.dumps(traj.to_json_dict(exclude_none=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "graph.json").write_text(
            json.dumps(graph.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "eval.json").write_text(
            json.dumps(report.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        html_path = write_report(
            session, graph, report, out_dir / "report.html",
            atif_validation=atif_val, annotations=annotations,
        )

    result = {
        "session_id": session.session_id,
        "team_name": session.team_name,
        "report": report,
        "graph": graph,
        "atif_valid": atif_val["valid"],
        "out_dir": out_dir,
        "html_path": html_path,
    }

    if register and sessions_dir is not None and write:
        from team_eval.harness.dataset import register_session

        register_session(
            sessions_dir,
            session_id=session.session_id,
            team_name=session.team_name,
            pattern=graph.pattern,
            health=report.score.get("health"),
            score_counts=report.score.get("counts"),
            source_path=str(Path(session_path).resolve()),
            captured_at=generated_at,
        )
    return result
