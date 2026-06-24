"""Evaluation data models.

Layered to mirror harbor's ``quality_checker`` / ``verifier`` and the richer
display-oriented structures from session-eval (DimensionScore, strengths/
weaknesses/suggestions) and astroneval (keypoint vs reason separation).

Grain ladder (coarse → fine):
    EvalReport
      ├─ checks[]           session-level checkpoints (unchanged from v0.1)
      ├─ role_evals[]       per-role evaluation, each with executions[]
      ├─ task_evals[]       per-task keypoints + completion
      └─ conclusion[]       the 7 universal angles → verdict + summary
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class CheckKind(str, Enum):
    objective = "objective"      # auto-evaluated from the parsed session
    subjective = "subjective"    # human / LLM judged against a rubric


class CheckOutcome(str, Enum):
    pass_ = "pass"
    fail = "fail"
    warn = "warn"
    na = "not_applicable"
    pending = "pending"          # subjective check not yet annotated/judged


class Severity(str, Enum):
    info = "info"
    minor = "minor"
    major = "major"
    critical = "critical"


class CheckEvidence(BaseModel):
    agent_id: str | None = None
    step_id: int | None = None
    role: str | None = None
    snippet: str | None = None
    ref_kind: str | None = None  # spawn|message|task|error|...


# --------------------------------------------------------------------------- #
# Rich, display-oriented structures (session-eval / astroneval inspired)
# --------------------------------------------------------------------------- #

class DimensionScore(BaseModel):
    """A single scored sub-dimension of a subjective judgement."""

    id: str
    name: str
    score: float | None = None      # 0-100
    analysis: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    """Structured judgement (LLM judge or human). Replaces a bare pass/fail."""

    passed: bool | None = None
    confidence: float | None = None     # 0-1
    reasoning: str | None = None        # the "reason" — why this verdict
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    evidence_refs: list[CheckEvidence] = Field(default_factory=list)
    judge_model: str | None = None
    cost_usd: float | None = None
    error: str | None = None            # set if judging failed


class Keypoint(BaseModel):
    """A single evaluation criterion for a task (the "keypoint")."""

    id: str
    description: str
    weight: float = 1.0
    type: str = "objective"             # objective|subjective
    outcome: CheckOutcome | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# Per-role / per-task grain
# --------------------------------------------------------------------------- #

class RoleExecution(BaseModel):
    """One transcript == one execution attempt of a role (churn = many)."""

    transcript_id: str
    attempt: int                        # 1-based among same role
    step_count: int
    has_final_text: bool
    error_count: int
    tool_calls: int
    tool_success: int
    tool_failure: int
    recovered: bool                     # had an error but later produced output
    first_ts: str | None = None
    last_ts: str | None = None


class RoleEval(BaseModel):
    """Evaluation of one role across all its execution attempts."""

    role: str
    executions: list[RoleExecution] = Field(default_factory=list)
    # objective roll-up
    completion: CheckOutcome = CheckOutcome.pending
    error_profile: CheckOutcome = CheckOutcome.pending
    step_count: int = 0
    total_errors: int = 0
    tool_success: int = 0
    tool_failure: int = 0
    churn: int = 0
    tool_summary: dict[str, Any] = Field(default_factory=dict)
    # subjective (LLM judge) — null until judged
    judgement: Verdict | None = None
    keypoints: list[Keypoint] = Field(default_factory=list)


class TaskEval(BaseModel):
    """Evaluation of one shared-list task."""

    task_id: str
    subject: str
    owner: str | None = None
    final_status: str = "pending"
    keypoints: list[Keypoint] = Field(default_factory=list)
    completion: CheckOutcome = CheckOutcome.pending
    evidence: list[CheckEvidence] = Field(default_factory=list)


class AngleConclusion(BaseModel):
    """One of the 7 universal evaluation angles → the conclusion it supports."""

    angle: str          # goal|planning|delegation|execution|robustness|efficiency|conformance
    question: str       # the question this angle answers
    verdict: CheckOutcome = CheckOutcome.pending
    summary: str = ""
    evidence: list[CheckEvidence] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Checkpoint + report
# --------------------------------------------------------------------------- #

class CheckPoint(BaseModel):
    id: str
    dimension: str               # one of the 7 angles
    title: str
    description: str
    kind: CheckKind
    severity: Severity = Severity.minor
    tags: list[str] = Field(default_factory=list)
    auto: bool = True
    outcome: CheckOutcome | None = None
    explanation: str | None = None
    evidence: list[CheckEvidence] = Field(default_factory=list)
    rubric: str | None = None
    metric: dict[str, Any] | None = None
    # v0.2: rich structured judgement for subjective checks
    verdict: Verdict | None = None
    dimension_scores: list[DimensionScore] = Field(default_factory=list)
    scope: str = "session"       # session|role:<role>|task:<id>


class EvalReport(BaseModel):
    session_id: str
    team_name: str | None = None
    pattern: str | None = None
    reduce_quality: str | None = None
    checks: list[CheckPoint] = Field(default_factory=list)
    role_evals: list[RoleEval] = Field(default_factory=list)
    task_evals: list[TaskEval] = Field(default_factory=list)
    conclusion: list[AngleConclusion] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    score: dict[str, Any] = Field(default_factory=dict)
    generated_at: str | None = None
    notes: list[str] = Field(default_factory=list)
