"""Evaluation framework: checkpoint catalog, per-role/per-task eval, cc-sdk judge."""

from team_eval.eval.checks import (
    ANGLES,
    CHECKS,
    DIMENSIONS,
    CheckDef,
    build_conclusion,
    build_eval_report,
)
from team_eval.eval.llm_judge import judge_report
from team_eval.eval.models import (
    AngleConclusion,
    CheckEvidence,
    CheckKind,
    CheckOutcome,
    CheckPoint,
    DimensionScore,
    EvalReport,
    Keypoint,
    RoleEval,
    RoleExecution,
    Severity,
    TaskEval,
    Verdict,
)

__all__ = [
    "ANGLES",
    "AngleConclusion",
    "CHECKS",
    "CheckDef",
    "CheckEvidence",
    "CheckKind",
    "CheckOutcome",
    "CheckPoint",
    "DIMENSIONS",
    "DimensionScore",
    "EvalReport",
    "Keypoint",
    "RoleEval",
    "RoleExecution",
    "Severity",
    "TaskEval",
    "Verdict",
    "build_conclusion",
    "build_eval_report",
    "judge_report",
]
