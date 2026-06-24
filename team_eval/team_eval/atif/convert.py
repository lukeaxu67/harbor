"""Convert a parsed TeamSession into an ATIF v1.7 Trajectory.

Root trajectory = the leader; each teammate transcript is embedded in
``subagent_trajectories[]`` (ATIF-v1.7). Delegation edges are expressed via
``SubagentTrajectoryRef`` attached to the leader's spawn (Agent) tool results.
"""

from __future__ import annotations

from typing import Any

from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    SubagentTrajectoryRef,
    ToolCall,
    Trajectory,
)

from team_eval.parse.models import AgentTranscript, StepRecord, TeamSession

SCHEMA = "ATIF-v1.7"


def _metrics_from_usage(usage: dict[str, Any] | None) -> Metrics | None:
    if not isinstance(usage, dict):
        return None
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
    inp = int(usage.get("input_tokens", 0) or 0)
    prompt = inp + cached + creation
    completion = int(usage.get("output_tokens", 0) or 0)
    extra = {k: v for k, v in usage.items() if k not in {
        "input_tokens", "output_tokens", "cache_read_input_tokens",
        "cache_creation_input_tokens",
    }}
    if prompt == 0 and completion == 0 and cached == 0 and not extra:
        return None
    return Metrics(
        prompt_tokens=prompt,
        completion_tokens=completion,
        cached_tokens=cached,
        cost_usd=None,
        extra=extra or None,
    )


def _step_to_atif(step: StepRecord) -> Step:
    """Convert one StepRecord into an ATIF Step (validators satisfied)."""
    common: dict[str, Any] = {
        "step_id": step.step_id,
        "timestamp": step.timestamp,
        "source": step.source,
    }

    if step.source == "agent":
        # ensure every tool_use has an id (required by ATIF ToolCall)
        call_ids: set[str] = set()
        tool_calls: list[ToolCall] = []
        for idx, tu in enumerate(step.tool_uses):
            cid = tu.tool_use_id or f"{step.step_id}-{idx}"
            call_ids.add(cid)
            tool_calls.append(
                ToolCall(
                    tool_call_id=cid,
                    function_name=tu.name,
                    arguments=tu.arguments or {},
                    extra={"raw_arguments": tu.raw} if tu.raw else None,
                )
            )

        # observation results: only keep those whose source_call_id is resolvable
        results: list[ObservationResult] = []
        for tr in step.tool_results:
            sid = tr.tool_use_id
            if sid is not None and sid not in call_ids:
                sid = None  # orphan → drop the dangling reference, keep content
            results.append(
                ObservationResult(
                    source_call_id=sid,
                    content=tr.content,
                )
            )

        atif_step = Step(
            message=step.text or "",
            tool_calls=tool_calls or None,
            observation=Observation(results=results) if results else None,
            llm_call_count=1,
            **common,
        )
        if step.reasoning:
            atif_step.reasoning_content = step.reasoning
        if step.model_name:
            atif_step.model_name = step.model_name
        m = _metrics_from_usage(step.usage)
        if m is not None:
            atif_step.metrics = m
        if step.extra:
            atif_step.extra = dict(step.extra)
        return atif_step

    # user / system step
    return Step(message=step.text or "", **common)


def _transcript_to_trajectory(
    transcript: AgentTranscript,
    *,
    trajectory_id: str,
    run_session_id: str,
    default_model: str | None,
) -> Trajectory:
    steps = [_step_to_atif(s) for s in transcript.steps]
    return Trajectory(
        schema_version=SCHEMA,
        session_id=run_session_id,
        trajectory_id=trajectory_id,
        agent=Agent(
            name=transcript.role,
            version="fleetview-teammate" if not transcript.is_leader else "fleetview-leader",
            model_name=default_model,
            extra={
                "is_leader": transcript.is_leader,
                "agent_id": transcript.agent_id,
                "teammate_id": transcript.teammate_id,
                "assigned_task_id": transcript.assigned_task_id,
                "transcript_files": transcript.transcript_files,
            },
        ),
        steps=steps,
        final_metrics=FinalMetrics(
            total_prompt_tokens=transcript.prompt_tokens or None,
            total_completion_tokens=transcript.completion_tokens or None,
            total_cached_tokens=transcript.cached_tokens or None,
            total_steps=len(steps),
        ),
        notes=transcript.assigned_task_snippet,
    )


def convert_to_atif(session: TeamSession, graph=None) -> Trajectory:
    """Build the root ATIF Trajectory with embedded subagent trajectories."""
    run_session_id = session.session_id

    # index subagents and map role -> [trajectory_id]
    role_to_tids: dict[str, list[str]] = {}
    embedded: list[Trajectory] = []
    default_model = None
    for sub in session.subagents:
        tid = sub.agent_id
        role_to_tids.setdefault(sub.role, []).append(tid)
        embedded.append(
            _transcript_to_trajectory(
                sub,
                trajectory_id=tid,
                run_session_id=run_session_id,
                default_model=default_model,
            )
        )

    # leader root trajectory
    leader = _transcript_to_trajectory(
        session.leader,
        trajectory_id=f"{run_session_id}:leader",
        run_session_id=run_session_id,
        default_model=default_model,
    )

    # attach SubagentTrajectoryRef to the leader's spawn (Agent) tool results,
    # linking each delegation to the embedded teammate trajectories of that role.
    role_of_spawn_step: dict[int, str] = {}
    for sp in session.spawns:
        if sp.name:
            role_of_spawn_step.setdefault(sp.step_id, sp.name)

    for step in leader.steps:
        if step.source != "agent" or not step.tool_calls:
            continue
        role = role_of_spawn_step.get(step.step_id)
        if not role or role not in role_to_tids:
            continue
        refs = [SubagentTrajectoryRef(trajectory_id=tid) for tid in role_to_tids[role]]
        if step.observation and step.observation.results:
            step.observation.results[0].subagent_trajectory_ref = refs
        else:
            step.observation = Observation(
                results=[
                    ObservationResult(
                        source_call_id=step.tool_calls[0].tool_call_id,
                        content=None,
                        subagent_trajectory_ref=refs,
                    )
                ]
            )

    # aggregate final metrics across leader + subagents
    total_prompt = session.leader.prompt_tokens + sum(s.prompt_tokens for s in session.subagents)
    total_completion = session.leader.completion_tokens + sum(s.completion_tokens for s in session.subagents)
    total_cached = session.leader.cached_tokens + sum(s.cached_tokens for s in session.subagents)
    leader.final_metrics = FinalMetrics(
        total_prompt_tokens=total_prompt or None,
        total_completion_tokens=total_completion or None,
        total_cached_tokens=total_cached or None,
        total_steps=len(leader.steps),
        extra={"includes_subagents": True, "subagent_count": len(embedded)},
    )

    # team-level metadata in root `extra` (does not break extra="forbid")
    churn = {}
    if graph is not None:
        churn = {
            n.role: n.transcript_count for n in graph.nodes if n.kind == "teammate"
        }
    leader.extra = {
        "team_name": session.team_name,
        "leader_role": session.leader_role,
        "pattern": graph.pattern if graph else None,
        "pattern_confidence": graph.pattern_confidence if graph else None,
        "reduce_quality": graph.reduce_quality if graph else None,
        "worker_count": graph.worker_count if graph else None,
        "task_final_statuses": {t.id: t.final_status for t in session.tasks},
        "transcript_churn_by_role": churn or None,
        "sendmessage_error_count": len(session.sendmessage_errors),
        "teamdelete_count": session.teamdelete_count,
    }
    leader.subagent_trajectories = embedded or None
    return leader
