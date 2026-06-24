"""Load a FleetView Team session directory into a TeamSession model.

Layout produced by Claude Code (FleetView teams):
    <projects>/<session_id>.jsonl           # leader / orchestrator transcript
    <projects>/<session_id>/subagents/agent-*.jsonl[+.meta.json]   # teammates
    <projects>/<session_id>/tool-results/*.txt                     # large tool outputs
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from team_eval.parse.event_normalize import normalize_events
from team_eval.parse.models import (
    AgentTranscript,
    SendMessageRecord,
    SpawnRecord,
    StepRecord,
    TeamSession,
    TeamTask,
    ToolUseRecord,
)

_TEAMMATE_MSG_RE = re.compile(
    r'<teammate-message[^>]*teammate_id="([^"]+)"[^>]*>', re.IGNORECASE
)
_TEAMMATE_SUMMARY_RE = re.compile(
    r'<teammate-message[^>]*summary="([^"]*)"', re.IGNORECASE
)
_TASK_NUM_RE = re.compile(r"TASK\s*\(?\s*#?(\d+)", re.IGNORECASE)
_INT_RE = re.compile(r"\d+")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _locate_session(path: str | Path) -> tuple[Path, Path, Path]:
    """Return (main_jsonl, subagents_dir, session_id_stem)."""
    p = Path(path).resolve()
    if p.is_file() and p.suffix == ".jsonl":
        stem = p.stem
        base = p.parent
        subagents = base / stem / "subagents"
        return p, subagents, stem
    if p.is_dir():
        # Could be the <stem>/ dir (has subagents) or the parent (has the jsonl).
        sub = p / "subagents"
        if sub.is_dir():
            stem = p.name
            main = p.parent / f"{stem}.jsonl"
            return main, sub, stem
        # Parent containing *.jsonl files.
        jsonls = sorted(p.glob("*.jsonl"))
        if jsonls:
            main = jsonls[0]
            stem = main.stem
            return main, p / stem / "subagents", stem
    raise FileNotFoundError(f"Could not locate a FleetView session at {path}")


def _summarize_transcript(t: AgentTranscript) -> None:
    """Fill derived summary fields on an AgentTranscript from its steps."""
    t.step_count = len(t.steps)
    timestamps = [s.timestamp for s in t.steps if s.timestamp]
    t.first_ts = timestamps[0] if timestamps else None
    t.last_ts = timestamps[-1] if timestamps else None
    tool_hist: Counter[str] = Counter()
    errors = 0
    assistant_turns = 0
    final_text = None
    prompt = completion = cached = 0
    for s in t.steps:
        if s.source == "agent":
            assistant_turns += 1
            if s.text.strip():
                final_text = s.text
            if isinstance(s.usage, dict):
                prompt += int(s.usage.get("input_tokens", 0) or 0)
                completion += int(s.usage.get("output_tokens", 0) or 0)
                cached += int(s.usage.get("cache_read_input_tokens", 0) or 0)
        for tu in s.tool_uses:
            tool_hist[tu.name] += 1
        for tr in s.tool_results:
            if tr.is_error:
                errors += 1
    t.assistant_turn_count = assistant_turns
    t.tool_hist = dict(tool_hist)
    t.error_count = errors
    t.prompt_tokens = prompt
    t.completion_tokens = completion
    t.cached_tokens = cached
    t.has_final_text = final_text is not None and bool(final_text.strip())
    t.final_text = (final_text[:2000] + "…") if final_text and len(final_text) > 2000 else final_text


def _build_transcript(
    agent_id: str,
    role: str,
    records: list[dict[str, Any]],
    *,
    is_leader: bool,
    transcript_files: list[str],
    session_id: str | None = None,
) -> AgentTranscript:
    steps = normalize_events(records)
    t = AgentTranscript(
        agent_id=agent_id,
        role=role,
        is_leader=is_leader,
        session_id=session_id,
        transcript_files=transcript_files,
        steps=steps,
    )
    # mine teammate context from the first user step
    for s in steps:
        if s.source == "user" and s.text:
            m = _TEAMMATE_MSG_RE.search(s.text)
            if m:
                t.teammate_id = m.group(1)
            sm = _TEAMMATE_SUMMARY_RE.search(s.text)
            if sm:
                t.assigned_task_snippet = sm.group(1)
            tn = _TASK_NUM_RE.search(s.text)
            if tn:
                t.assigned_task_id = f"#{tn.group(1)}"
            break
    _summarize_transcript(t)
    return t


def _match_result(step: StepRecord, tu: ToolUseRecord) -> dict[str, Any] | None:
    for tr in step.tool_results:
        if tr.tool_use_id and tr.tool_use_id == tu.tool_use_id:
            return {"content": tr.content, "is_error": tr.is_error}
    return None


def load_team_session(path: str | Path) -> TeamSession:
    """Parse a full team session (leader + subagents) into a TeamSession."""
    main_jsonl, subagents_dir, stem = _locate_session(path)

    leader_records = _read_jsonl(main_jsonl)
    raw_counts: Counter[str] = Counter(r.get("type", "?") for r in leader_records)

    leader = _build_transcript(
        agent_id="leader",
        role="team-lead",
        records=leader_records,
        is_leader=True,
        transcript_files=[str(main_jsonl)],
        session_id=stem,
    )

    # ---- load subagents ----
    subagents: list[AgentTranscript] = []
    if subagents_dir.is_dir():
        for jsonl_file in sorted(subagents_dir.glob("agent-*.jsonl")):
            if jsonl_file.name.endswith(".meta.json"):
                continue
            agent_id = jsonl_file.stem.replace("agent-", "")
            meta = _read_meta(jsonl_file.with_suffix(".meta.json"))
            role = meta.get("agentType") or "unknown"
            recs = _read_jsonl(jsonl_file)
            subagents.append(
                _build_transcript(
                    agent_id=agent_id,
                    role=role,
                    records=recs,
                    is_leader=False,
                    transcript_files=[str(jsonl_file)],
                )
            )

    # ---- mine leader coordination signals ----
    tasks: dict[str, TeamTask] = {}
    task_order: list[str] = []
    spawns: list[SpawnRecord] = []
    messages: list[SendMessageRecord] = []
    sendmessage_errors: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    teamdelete_count = 0
    taskstop_targets: list[str] = []
    team_name: str | None = None
    leader_role = "team-lead"
    next_create_id = 1

    def get_or_create(tid: str, subject: str = "", description: str = "") -> TeamTask:
        nonlocal next_create_id
        if tid not in tasks:
            tasks[tid] = TeamTask(id=tid, subject=subject, description=description)
            task_order.append(tid)
            try:
                if int(tid) >= next_create_id:
                    next_create_id = int(tid) + 1
            except ValueError:
                pass
        return tasks[tid]

    for step in leader.steps:
        for tu in step.tool_uses:
            name = tu.name
            args = tu.arguments or {}
            counts[name] += 1
            result = _match_result(step, tu)

            if name == "TeamCreate":
                team_name = args.get("team_name") or team_name
                leader_role = args.get("agent_type") or leader_role
            elif name == "Agent":
                spawns.append(
                    SpawnRecord(
                        step_id=step.step_id,
                        name=args.get("name"),
                        subagent_type=args.get("subagent_type"),
                        team_name=args.get("team_name"),
                        description=args.get("description"),
                    )
                )
            elif name == "SendMessage":
                ok = True
                err = None
                if result and result.get("is_error"):
                    ok = False
                    err = (result.get("content") or "")[:300]
                elif result is None:
                    ok = False
                    err = "no tool_result observed"
                msg_preview = None
                mv = args.get("message")
                if isinstance(mv, str):
                    msg_preview = mv[:300]
                rec = SendMessageRecord(
                    step_id=step.step_id,
                    to=args.get("to"),
                    summary=args.get("summary"),
                    text_preview=msg_preview,
                    ok=ok,
                    error=err,
                )
                messages.append(rec)
                if not ok:
                    sendmessage_errors.append(
                        {"step_id": step.step_id, "to": args.get("to"), "error": err}
                    )
            elif name == "TaskCreate":
                tid = str(args.get("taskId") or args.get("id") or next_create_id)
                if "taskId" not in args and "id" not in args:
                    # try to read id from the tool result
                    if result and isinstance(result.get("content"), str):
                        m = _INT_RE.search(result["content"])
                        if m:
                            tid = m.group(0)
                    if tid == str(next_create_id):
                        next_create_id += 1
                get_or_create(
                    tid,
                    subject=str(args.get("subject", "")),
                    description=str(args.get("description", "")),
                ).history.append(
                    {"action": "create", "ts": step.timestamp, "step_id": step.step_id}
                )
            elif name == "TaskUpdate":
                tid = str(args.get("taskId") or "")
                if tid:
                    tk = get_or_create(tid)
                    if args.get("subject"):
                        tk.subject = str(args["subject"])
                    if args.get("owner"):
                        tk.owner = str(args["owner"])
                    status = args.get("status")
                    entry: dict[str, Any] = {
                        "action": "update",
                        "ts": step.timestamp,
                        "step_id": step.step_id,
                    }
                    if status:
                        entry["status"] = str(status)
                        tk.final_status = str(status)
                    if args.get("owner"):
                        entry["owner"] = str(args["owner"])
                    tk.history.append(entry)
            elif name == "TeamDelete":
                teamdelete_count += 1
            elif name == "TaskStop":
                tgt = args.get("task_id") or args.get("shell_id")
                if tgt:
                    taskstop_targets.append(str(tgt))

    # tasks that were referenced but never created: keep them too
    tasks_list = [tasks[k] for k in task_order]

    all_ts = [
        s.timestamp
        for t in [leader, *subagents]
        for s in t.steps
        if s.timestamp
    ]

    session = TeamSession(
        session_dir=str(main_jsonl.parent),
        session_id=stem,
        team_name=team_name,
        leader_role=leader_role,
        leader=leader,
        subagents=subagents,
        tasks=tasks_list,
        spawns=spawns,
        messages=messages,
        counts=dict(counts),
        sendmessage_errors=sendmessage_errors,
        teamdelete_count=teamdelete_count,
        taskstop_targets=taskstop_targets,
        first_ts=min(all_ts) if all_ts else None,
        last_ts=max(all_ts) if all_ts else None,
        raw_record_counts=dict(raw_counts),
    )

    # record the original user task as the first leader user step
    for s in leader.steps:
        if s.source == "user" and s.text:
            session.notes.append(f"original_task: {s.text[:300]}")
            break
    if subagents:
        session.notes.append(
            f"parsed {len(subagents)} subagent transcripts across "
            f"{len({t.role for t in subagents})} roles"
        )
    return session
