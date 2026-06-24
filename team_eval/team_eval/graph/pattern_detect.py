"""Detect the team's collaboration pattern (pipeline / mapreduce / supervisor / custom)."""

from __future__ import annotations

from team_eval.graph.models import TeamGraph
from team_eval.parse.models import TeamSession

LEADER_ID = "team-lead"
_SYNTH_LEN = 800  # chars; a real synthesis is usually longer than idle chatter
_IDLE_MARKERS = ("idle", "standing by", "no action needed", "awaiting", "holding for")


def _workers_parallel(session: TeamSession) -> bool:
    starts = [s.first_ts for s in session.subagents if s.first_ts]
    ends = [s.last_ts for s in session.subagents if s.last_ts]
    if len(starts) < 2:
        return False
    # parallel if the earliest end is after the latest start (windows overlap)
    return min(ends) > max(starts)


def _reduce_quality(session: TeamSession) -> tuple[str, bool]:
    """Return (reduce_quality, has_any_synthesis)."""
    leader = session.leader
    sub_end = max(
        (s.last_ts for s in session.subagents if s.last_ts), default=""
    )
    max_len = 0
    synthesis_after_workers = False
    for step in leader.steps:
        if step.source != "agent" or not step.text:
            continue
        ln = len(step.text)
        if ln > max_len:
            max_len = ln
        if step.timestamp and step.timestamp > sub_end and ln >= 400:
            low = step.text.lower()
            if not any(m in low for m in _IDLE_MARKERS):
                synthesis_after_workers = True
    has_synthesis = max_len >= _SYNTH_LEN
    if has_synthesis and synthesis_after_workers:
        return "complete", True
    if has_synthesis:
        return "partial", True
    return "none", False


def detect_pattern(graph: TeamGraph, session: TeamSession) -> None:
    leader_id = session.leader_role or LEADER_ID
    workers = [n for n in graph.nodes if n.kind == "teammate"]
    worker_count = len(workers)

    spawn_dsts = {
        e.dst for e in graph.edges if e.kind == "spawn" and e.src == leader_id
    }
    fanout = len(spawn_dsts)
    parallel = _workers_parallel(session)
    reduce_quality, has_synthesis = _reduce_quality(session)

    reasons: list[str] = []
    pattern = "custom"
    confidence = 0.3

    if worker_count >= 2 and fanout >= 2 and graph.peer_message_edges == 0:
        # star topology with fan-out → mapreduce or supervisor
        pattern = "mapreduce" if parallel else "supervisor"
        confidence = 0.85 if parallel else 0.6
        reasons.append(
            f"single leader fans out to {fanout} worker role(s) "
            f"({'parallel' if parallel else 'sequential'} execution, no peer messaging)"
        )
        if pattern == "mapreduce":
            reasons.append(
                f"reduce step quality: {reduce_quality} "
                f"({'synthesis present' if has_synthesis else 'no synthesis detected'})"
            )
    elif graph.peer_message_edges > 0:
        # workers hand off to each other → pipeline-ish
        pattern = "pipeline"
        confidence = 0.55
        reasons.append(
            f"{graph.peer_message_edges} peer-to-peer message edge(s) suggest "
            f"stage handoff between workers"
        )
    else:
        pattern = "supervisor" if worker_count >= 1 else "custom"
        confidence = 0.4
        reasons.append("leader dispatches ad hoc to workers without clear fan-out")

    graph.pattern = pattern
    graph.pattern_confidence = round(confidence, 2)
    graph.pattern_reason = "; ".join(reasons) if reasons else "undetermined"
    graph.reduce_quality = reduce_quality
