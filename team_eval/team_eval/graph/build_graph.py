"""Build a TeamGraph (nodes + edges + tasks) from a parsed TeamSession."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from team_eval.graph.models import AgentNode, Edge, TeamGraph
from team_eval.parse.models import TeamSession


def _ts_min(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b


def _ts_max(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b


def build_team_graph(session: TeamSession) -> TeamGraph:
    leader_id = session.leader_role or "team-lead"

    # ---- nodes ----
    leader = AgentNode(
        id=leader_id,
        role=session.leader_role or "team-lead",
        kind="leader",
        transcript_count=1,
        transcript_ids=[session.leader.agent_id],
        step_count=session.leader.step_count,
        assistant_turn_count=session.leader.assistant_turn_count,
        prompt_tokens=session.leader.prompt_tokens,
        completion_tokens=session.leader.completion_tokens,
        cached_tokens=session.leader.cached_tokens,
        active_start=session.leader.first_ts,
        active_end=session.leader.last_ts,
        has_final_text=session.leader.has_final_text,
        error_count=session.leader.error_count,
    )

    role_nodes: dict[str, AgentNode] = {}
    role_tasks: dict[str, list[str]] = defaultdict(list)
    for tk in session.tasks:
        if tk.owner:
            role_tasks[tk.owner].append(tk.id)

    # aggregate subagents by role
    for sub in session.subagents:
        node = role_nodes.get(sub.role)
        if node is None:
            node = AgentNode(
                id=sub.role,
                role=sub.role,
                kind="teammate",
                task_ids=role_tasks.get(sub.role, []),
            )
            role_nodes[sub.role] = node
        node.transcript_count += 1
        node.transcript_ids.append(sub.agent_id)
        node.step_count += sub.step_count
        node.assistant_turn_count += sub.assistant_turn_count
        node.prompt_tokens += sub.prompt_tokens
        node.completion_tokens += sub.completion_tokens
        node.cached_tokens += sub.cached_tokens
        node.active_start = _ts_min(node.active_start, sub.first_ts)
        node.active_end = _ts_max(node.active_end, sub.last_ts)
        node.has_final_text = node.has_final_text or sub.has_final_text
        node.error_count += sub.error_count

    nodes = [leader, *role_nodes.values()]
    node_ids = {n.id for n in nodes}

    # ---- edges ----
    edge_map: dict[tuple[str, str, str], Edge] = {}

    def add_edge(src: str, dst: str, kind: str, ts: str | None = None) -> None:
        # normalize leader alias
        dst_n = leader_id if dst in {"team-lead", "leader"} else dst
        src_n = leader_id if src in {"team-lead", "leader"} else src
        key = (src_n, dst_n, kind)
        e = edge_map.get(key)
        if e is None:
            e = Edge(src=src_n, dst=dst_n, kind=kind, count=0)  # type: ignore[arg-type]
            edge_map[key] = e
        e.count += 1
        e.first_ts = _ts_min(e.first_ts, ts)
        e.last_ts = _ts_max(e.last_ts, ts)

    # spawn edges (leader Agent tool_use -> teammate)
    for sp in session.spawns:
        add_edge(leader_id, sp.name or "", "spawn")

    # message edges: leader SendMessage + subagent SendMessage
    for m in session.messages:
        add_edge(leader_id, m.to or "", "message")
    for sub in session.subagents:
        for step in sub.steps:
            for tu in step.tool_uses:
                if tu.name == "SendMessage":
                    add_edge(sub.role, tu.arguments.get("to") or "", "message", step.timestamp)

    # task assignment edges (leader -> owner)
    for tk in session.tasks:
        if tk.owner:
            add_edge(leader_id, tk.owner, "task_assign")

    edges = list(edge_map.values())
    orphan_targets = sorted(
        {e.dst for e in edges if e.dst not in node_ids and e.dst != ""}
    )

    graph = TeamGraph(
        team_name=session.team_name,
        session_id=session.session_id,
        nodes=nodes,
        edges=edges,
        tasks=[t.model_dump() for t in session.tasks],
        worker_count=len(role_nodes),
        peer_message_edges=sum(
            1 for e in edges
            if e.kind == "message"
            and e.src != leader_id
            and e.dst != leader_id
        ),
        orphan_targets=orphan_targets,
    )

    # pattern detection (fills pattern/confidence/reason/reduce_quality)
    from team_eval.graph.pattern_detect import detect_pattern

    detect_pattern(graph, session)
    return graph
