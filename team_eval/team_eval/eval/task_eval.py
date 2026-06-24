"""Per-task evaluation: keypoints + completion for each shared-list task."""

from __future__ import annotations

from team_eval.eval.models import CheckEvidence, CheckOutcome, Keypoint, RoleEval, TaskEval
from team_eval.parse.models import TeamSession


def _role_delivered(role_evals: list[RoleEval], role: str | None) -> bool:
    if not role:
        return False
    for re_ in role_evals:
        if re_.role == role:
            return any(e.has_final_text for e in re_.executions)
    return False


def build_task_evals(session: TeamSession, role_evals: list[RoleEval]) -> list[TaskEval]:
    """Build a TaskEval per shared-list task with derived keypoints."""
    out: list[TaskEval] = []
    for tk in session.tasks:
        delivered = _role_delivered(role_evals, tk.owner)
        keypoints = [
            Keypoint(
                id=f"{tk.id}.assigned",
                description="Task assigned to an existing role",
                outcome=CheckOutcome.pass_ if tk.owner else CheckOutcome.fail,
            ),
            Keypoint(
                id=f"{tk.id}.executed",
                description=f"Owner role ({tk.owner or '?'}) produced a deliverable",
                outcome=CheckOutcome.pass_ if delivered else CheckOutcome.fail,
            ),
            Keypoint(
                id=f"{tk.id}.closed",
                description="Task reached 'completed' in the shared list",
                outcome=(CheckOutcome.pass_ if tk.final_status == "completed"
                         else CheckOutcome.fail),
            ),
            Keypoint(
                id=f"{tk.id}.quality",
                description="Deliverable satisfies the task's intent (subjective)",
                type="subjective",
                outcome=CheckOutcome.pending,
            ),
        ]
        # completion roll-up: closed→pass; delivered-but-not-closed→warn; else fail
        if tk.final_status == "completed":
            completion = CheckOutcome.pass_
        elif delivered:
            completion = CheckOutcome.warn
        else:
            completion = CheckOutcome.fail
        out.append(
            TaskEval(
                task_id=tk.id,
                subject=tk.subject,
                owner=tk.owner,
                final_status=tk.final_status,
                keypoints=keypoints,
                completion=completion,
                evidence=[CheckEvidence(role=tk.owner, ref_kind="task",
                                        snippet=f"#{tk.id} {tk.final_status}: {tk.subject[:50]}")],
            )
        )
    return out
