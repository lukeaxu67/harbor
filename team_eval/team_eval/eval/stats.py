"""Aggregate statistics for a team session (consumed by eval report + viz)."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from team_eval.graph.models import TeamGraph
from team_eval.parse.models import TeamSession


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _span_seconds(first: str | None, last: str | None) -> float | None:
    a, b = _parse_ts(first), _parse_ts(last)
    if not a or not b:
        return None
    return (b - a).total_seconds()


def compute_stats(session: TeamSession, graph: TeamGraph) -> dict[str, Any]:
    leader = session.leader
    subs = session.subagents

    role_count = Counter(s.role for s in subs)
    churn_by_role = {n.role: n.transcript_count for n in graph.nodes if n.kind == "teammate"}

    tool_hist: Counter[str] = Counter()
    for t in [leader, *subs]:
        tool_hist.update(t.tool_hist)

    total_prompt = leader.prompt_tokens + sum(s.prompt_tokens for s in subs)
    total_completion = leader.completion_tokens + sum(s.completion_tokens for s in subs)
    total_cached = leader.cached_tokens + sum(s.cached_tokens for s in subs)
    total_errors = leader.error_count + sum(s.error_count for s in subs)

    leader_out = sum(e.count for e in graph.edges if e.kind == "message" and e.src == leader.role)
    teammate_in = sum(
        e.count for e in graph.edges if e.kind == "message" and e.dst == leader.role
    )

    # parallelism: fraction of worker active windows overlapping the union window
    sub_starts = [s.first_ts for s in subs if s.first_ts]
    sub_ends = [s.last_ts for s in subs if s.last_ts]
    parallel = bool(sub_starts) and min(sub_ends) > max(sub_starts)

    task_status = Counter(t.final_status for t in session.tasks)

    return {
        "team_name": session.team_name,
        "leader": {
            "steps": leader.step_count,
            "assistant_turns": leader.assistant_turn_count,
            "tokens": {
                "prompt": leader.prompt_tokens,
                "completion": leader.completion_tokens,
                "cached": leader.cached_tokens,
            },
            "errors": leader.error_count,
            "has_final_text": leader.has_final_text,
        },
        "subagents": {
            "count": len(subs),
            "by_role": dict(role_count),
            "roles": len(role_count),
        },
        "totals": {
            "steps": leader.step_count + sum(s.step_count for s in subs),
            "tokens": {
                "prompt": total_prompt,
                "completion": total_completion,
                "cached": total_cached,
                "all": total_prompt + total_completion + total_cached,
            },
            "errors": total_errors,
            "tool_calls": sum(tool_hist.values()),
        },
        "tool_hist": dict(tool_hist),
        "messages": {
            "leader_outgoing": leader_out,
            "teammate_to_leader": teammate_in,
            "peer": graph.peer_message_edges,
            "send_errors": len(session.sendmessage_errors),
        },
        "spawns": len(session.spawns),
        "tasks": {
            "total": len(session.tasks),
            "by_status": dict(task_status),
        },
        "teamdelete_count": session.teamdelete_count,
        "taskstop_targets": session.taskstop_targets,
        "churn": {
            "by_role": churn_by_role,
            "max": max(churn_by_role.values()) if churn_by_role else 0,
        },
        "wall_clock_sec": _span_seconds(session.first_ts, session.last_ts),
        "worker_parallel": parallel,
        "span": {"first": session.first_ts, "last": session.last_ts},
        "pattern": graph.pattern,
        "reduce_quality": graph.reduce_quality,
    }
