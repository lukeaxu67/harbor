"""Intermediate data model for a parsed FleetView Team session.

This is the single source of truth consumed by graph/, atif/, eval/ and viz/.
Pydantic v2 models so they serialize cleanly to graph.json / eval.json.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Source = Literal["system", "user", "agent"]


def _truncate(text: Any, n: int = 4000) -> str:
    """Best-effort stringify + truncate for storage / display."""
    if text is None:
        return ""
    if isinstance(text, (list, dict)):
        s = _stringify(text)
    else:
        s = str(text)
    s = s.strip()
    if len(s) > n:
        return s[:n] + f"\n…[truncated, {len(s)} chars total]"
    return s


def _stringify(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if isinstance(x, (list, dict)):
        try:
            import json

            return json.dumps(x, ensure_ascii=False)
        except Exception:
            return str(x)
    return str(x)


class ToolUseRecord(BaseModel):
    """A tool_use block from an assistant turn."""

    tool_use_id: str | None = None
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw: str | None = None  # original argument JSON if parsing was needed


class ToolResultRecord(BaseModel):
    """A tool_result block following a tool_use (environment feedback)."""

    tool_use_id: str | None = None
    content: str | None = None  # truncated preview
    is_error: bool = False


class StepRecord(BaseModel):
    """One normalized turn (mirrors ATIF Step grain): a user/agent/system turn,
    with agent turns carrying their tool_uses and the following tool_results
    collapsed into `tool_results` (→ ATIF observation)."""

    step_id: int
    timestamp: str | None = None
    source: Source
    text: str = ""
    reasoning: str | None = None
    model_name: str | None = None
    tool_uses: list[ToolUseRecord] = Field(default_factory=list)
    tool_results: list[ToolResultRecord] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
    raw_uuid: str | None = None
    is_sidechain: bool = False
    extra: dict[str, Any] | None = None  # team-specific mined signals


class TeamTask(BaseModel):
    """A shared task-list entry created/updated via TaskCreate/TaskUpdate."""

    id: str
    subject: str = ""
    description: str = ""
    owner: str | None = None  # owning role
    history: list[dict[str, Any]] = Field(default_factory=list)
    final_status: str = "pending"  # pending|in_progress|completed|deleted|unknown


class AgentTranscript(BaseModel):
    """One agent's transcript: the leader (main session) or a teammate."""

    agent_id: str
    role: str  # agentType; leader uses its role string
    is_leader: bool = False
    session_id: str | None = None
    transcript_files: list[str] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)

    # mined team context (teammates)
    teammate_id: str | None = None
    assigned_task_id: str | None = None  # e.g. "#3"
    assigned_task_snippet: str | None = None

    # derived summary
    first_ts: str | None = None
    last_ts: str | None = None
    step_count: int = 0
    assistant_turn_count: int = 0
    final_text: str | None = None  # last agent text turn
    has_final_text: bool = False
    error_count: int = 0
    tool_hist: dict[str, int] = Field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float | None = None


class SendMessageRecord(BaseModel):
    """A leader/team SendMessage tool_use (inter-agent message edge)."""

    step_id: int
    to: str | None = None
    summary: str | None = None
    text_preview: str | None = None
    ok: bool = True
    error: str | None = None


class SpawnRecord(BaseModel):
    """A leader Agent tool_use that spawns/addresses a teammate."""

    step_id: int
    name: str | None = None  # teammate name
    subagent_type: str | None = None
    team_name: str | None = None
    description: str | None = None


class TeamSession(BaseModel):
    """Fully parsed team session."""

    session_dir: str
    session_id: str
    team_name: str | None = None
    leader_role: str = "team-lead"
    leader: AgentTranscript
    subagents: list[AgentTranscript] = Field(default_factory=list)
    tasks: list[TeamTask] = Field(default_factory=list)

    # leader-side coordination signals
    spawns: list[SpawnRecord] = Field(default_factory=list)
    messages: list[SendMessageRecord] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    sendmessage_errors: list[dict[str, Any]] = Field(default_factory=list)
    teamdelete_count: int = 0
    taskstop_targets: list[str] = Field(default_factory=list)

    first_ts: str | None = None
    last_ts: str | None = None
    raw_record_counts: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)  # parser-level observations
