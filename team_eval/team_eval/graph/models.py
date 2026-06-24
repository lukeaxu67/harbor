"""Execution-graph data model."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

NodeKind = Literal["leader", "teammate"]
EdgeKind = Literal["spawn", "message", "task_assign", "task_update"]


class AgentNode(BaseModel):
    id: str  # role string (leader or a teammate role)
    role: str
    kind: NodeKind
    transcript_count: int = 0
    transcript_ids: list[str] = Field(default_factory=list)
    step_count: int = 0
    assistant_turn_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    active_start: str | None = None
    active_end: str | None = None
    has_final_text: bool = False
    error_count: int = 0
    task_ids: list[str] = Field(default_factory=list)
    # filled by eval overlay (not by the graph builder)
    verdict: str | None = None  # pass|warn|fail|None


class Edge(BaseModel):
    src: str  # node id
    dst: str  # node id
    kind: EdgeKind
    count: int = 0
    first_ts: str | None = None
    last_ts: str | None = None


class TeamGraph(BaseModel):
    team_name: str | None = None
    session_id: str
    nodes: list[AgentNode] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    tasks: list[dict] = Field(default_factory=list)
    pattern: str = "custom"
    pattern_confidence: float = 0.0
    pattern_reason: str = ""
    reduce_quality: str | None = None  # none|partial|complete
    worker_count: int = 0
    peer_message_edges: int = 0
    orphan_targets: list[str] = Field(default_factory=list)  # edges to nonexistent nodes
