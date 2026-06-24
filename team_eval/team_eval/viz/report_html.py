"""Render a self-contained interactive HTML report (topology + timeline + steps + eval).

Data is inlined as JSON; topology uses vis-network from CDN; the timeline,
step drill-down and eval panels are vanilla JS/CSS so they work even offline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Template

from team_eval.atif.validate import validate_atif
from team_eval.eval.models import CheckPoint, EvalReport
from team_eval.graph.models import TeamGraph
from team_eval.parse.models import TeamSession

TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.html.j2"
_TEXT = 700
_ARG = 180
_OBS = 320


def _role_palette() -> dict[str, str]:
    return {
        "team-lead": "#f59e0b",
        "arch-researcher": "#60a5fa",
        "cli-researcher": "#34d399",
        "agents-env-researcher": "#a78bfa",
        "benchmarks-researcher": "#f472b6",
        "infra-researcher": "#fb923c",
    }


def _node_verdict(node, session: TeamSession) -> str:
    if node.kind == "leader":
        return "info"
    if node.error_count > 10 or not node.has_final_text:
        return "fail"
    if node.error_count > 0 or node.transcript_count > 2:
        return "warn"
    return "pass"


def _steps_for_node(role: str, session: TeamSession) -> list[dict[str, Any]]:
    # representative transcript = the one with the most steps for this role
    candidates = [s for s in session.subagents if s.role == role] or (
        [session.leader] if role == session.leader_role else []
    )
    if not candidates:
        return []
    rep = max(candidates, key=lambda t: t.step_count)
    out = []
    for s in rep.steps:
        tools = []
        for tu in s.tool_uses:
            try:
                args_preview = json.dumps(tu.arguments, ensure_ascii=False)
            except (TypeError, ValueError):
                args_preview = str(tu.arguments)
            tools.append({"name": tu.name, "args": args_preview[:_ARG]})
        obs = []
        has_error = False
        for tr in s.tool_results:
            if tr.is_error:
                has_error = True
            obs.append({"preview": (tr.content or "")[:_OBS], "is_error": tr.is_error})
        out.append({
            "step_id": s.step_id,
            "source": s.source,
            "text": (s.text or "")[:_TEXT],
            "reasoning": (s.reasoning or "")[:_TEXT] if s.reasoning else None,
            "model": s.model_name,
            "tools": tools,
            "obs": obs,
            "has_error": has_error,
            "ts": s.timestamp,
        })
    return out


def _build_seq_events(session: TeamSession) -> list[dict[str, Any]]:
    """Unified, time-ordered event stream for the sequence diagram.

    Inter-agent interactions (spawn / message / task) + error markers — the
    things topology & timeline cannot show together. Per-step tool turns stay
    in ``steps_by_node`` for lane expansion.
    """
    leader_ts = {s.step_id: s.timestamp for s in session.leader.steps}
    leader = session.leader_role or "team-lead"
    events: list[dict[str, Any]] = []

    # original task kickoff
    for s in session.leader.steps:
        if s.source == "user" and s.text:
            events.append({"ts": s.timestamp, "from": None, "to": leader,
                           "kind": "task", "label": "任务下发",
                           "detail": s.text[:160], "role": leader})
            break

    for sp in session.spawns:
        events.append({"ts": leader_ts.get(sp.step_id), "from": leader, "to": sp.name,
                       "kind": "spawn", "label": "spawn",
                       "detail": sp.description or "", "role": sp.name})

    for m in session.messages:
        events.append({"ts": leader_ts.get(m.step_id), "from": leader, "to": m.to,
                       "kind": "message", "label": (m.summary or "msg")[:40],
                       "detail": m.text_preview or "", "ok": m.ok,
                       "error": m.error, "role": m.to})

    # teammate → leader messages + errors (per transcript)
    for sub in session.subagents:
        for step in sub.steps:
            for tu in step.tool_uses:
                if tu.name == "SendMessage":
                    events.append({"ts": step.timestamp, "from": sub.role,
                                   "to": leader, "kind": "message",
                                   "label": (tu.arguments.get("summary") or "reply")[:40],
                                   "detail": str(tu.arguments.get("message") or "")[:160],
                                   "role": sub.role})
            for tr in step.tool_results:
                if tr.is_error:
                    events.append({"ts": step.timestamp, "from": sub.role, "to": sub.role,
                                   "kind": "error", "label": "error",
                                   "detail": (tr.content or "")[:120], "role": sub.role})

    # task lifecycle
    for tk in session.tasks:
        for h in tk.history:
            events.append({"ts": h.get("ts"), "from": leader, "to": tk.owner or leader,
                           "kind": "task", "label": f"task#{tk.id}:{h.get('action')}",
                           "detail": f"{tk.subject[:50]} → {h.get('status') or h.get('action')}",
                           "role": tk.owner or leader})

    # stable sort by ts (events without ts keep order)
    events.sort(key=lambda e: e.get("ts") or "")
    return events


def build_payload(
    session: TeamSession,
    graph: TeamGraph,
    report: EvalReport,
    atif_validation: dict[str, Any],
    annotations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    palette = _role_palette()
    nodes = []
    for n in graph.nodes:
        verdict = _node_verdict(n, session)
        nodes.append({
            "id": n.id,
            "role": n.role,
            "kind": n.kind,
            "color": palette.get(n.role, "#94a3b8"),
            "transcript_count": n.transcript_count,
            "transcript_ids": n.transcript_ids,
            "step_count": n.step_count,
            "tokens": n.prompt_tokens + n.completion_tokens + n.cached_tokens,
            "active_start": n.active_start,
            "active_end": n.active_end,
            "has_final_text": n.has_final_text,
            "error_count": n.error_count,
            "task_ids": n.task_ids,
            "verdict": verdict,
        })

    steps_by_node = {n["id"]: _steps_for_node(n["role"], session) for n in nodes}

    checks = []
    for c in report.checks:
        checks.append({
            "id": c.id,
            "dimension": c.dimension,
            "title": c.title,
            "description": c.description,
            "kind": c.kind.value,
            "severity": c.severity.value,
            "tags": c.tags,
            "auto": c.auto,
            "outcome": c.outcome.value if c.outcome else "pending",
            "explanation": c.explanation,
            "evidence": [e.model_dump(exclude_none=True) for e in c.evidence],
            "rubric": c.rubric,
            "metric": c.metric,
        })

    lanes = [n["id"] for n in nodes]  # leader first, then teammate roles
    return {
        "meta": {
            "session_id": session.session_id,
            "team_name": session.team_name,
            "pattern": graph.pattern,
            "pattern_confidence": graph.pattern_confidence,
            "pattern_reason": graph.pattern_reason,
            "reduce_quality": graph.reduce_quality,
            "worker_count": graph.worker_count,
            "span_first": session.first_ts,
            "span_last": session.last_ts,
            "wall_clock_sec": report.stats.get("wall_clock_sec"),
            "health": report.score.get("health"),
            "score_counts": report.score.get("counts"),
        },
        "nodes": nodes,
        "edges": [e.model_dump() for e in graph.edges],
        "tasks": graph.tasks,
        "checks": checks,
        "dimensions": ["structural", "execution", "coordination", "outcome",
                       "efficiency", "robustness", "atif"],
        "conclusion": [c.model_dump(exclude_none=True) for c in report.conclusion],
        "role_evals": [r.model_dump(exclude_none=True) for r in report.role_evals],
        "task_evals": [t.model_dump(exclude_none=True) for t in report.task_evals],
        "stats": report.stats,
        "steps_by_node": steps_by_node,
        "lanes": lanes,
        "seq_events": _build_seq_events(session),
        "annotations": annotations or {},
        "atif_valid": atif_validation.get("valid"),
    }


def render_report(
    session: TeamSession,
    graph: TeamGraph,
    report: EvalReport,
    atif_validation: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
) -> str:
    atif_validation = atif_validation or {"valid": None, "checks": []}
    payload = build_payload(session, graph, report, atif_validation, annotations)
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.render(payload_json=json.dumps(payload, ensure_ascii=False))


def write_report(
    session: TeamSession,
    graph: TeamGraph,
    report: EvalReport,
    out_path: str | Path,
    atif_validation: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
) -> Path:
    html = render_report(session, graph, report, atif_validation, annotations)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
