"""Per-role evaluation: group a role's transcripts into executions + roll-up."""

from __future__ import annotations

from collections import Counter

from team_eval.eval.error_correction import detect_recovery
from team_eval.eval.models import CheckOutcome, Keypoint, RoleEval, RoleExecution
from team_eval.parse.models import TeamSession

_ERR_THRESH = 10  # per-execution tool-error threshold


def _tool_stats(transcript) -> tuple[int, int, int]:
    """Return (tool_calls, tool_success, tool_failure) for one transcript."""
    calls = 0
    results = 0
    failure = 0
    for step in transcript.steps:
        if step.source != "agent":
            continue
        calls += len(step.tool_uses)
        for tr in step.tool_results:
            results += 1
            if tr.is_error:
                failure += 1
    success = max(0, results - failure)
    return calls, success, failure


def build_role_evals(session: TeamSession) -> list[RoleEval]:
    """Build a RoleEval per teammate role, with one RoleExecution per transcript."""
    # group transcripts by role, preserving attempt order by first_ts
    by_role: dict[str, list] = {}
    for sub in session.subagents:
        by_role.setdefault(sub.role, []).append(sub)
    for role in by_role:
        by_role[role].sort(key=lambda t: t.first_ts or "")

    role_evals: list[RoleEval] = []
    for role, transcripts in by_role.items():
        executions: list[RoleExecution] = []
        total_errors = 0
        total_success = 0
        total_failure = 0
        tool_hist: Counter[str] = Counter()
        for idx, t in enumerate(transcripts, start=1):
            rec = detect_recovery(t)
            calls, succ, fail = _tool_stats(t)
            tool_hist.update(t.tool_hist)
            executions.append(
                RoleExecution(
                    transcript_id=t.agent_id,
                    attempt=idx,
                    step_count=t.step_count,
                    has_final_text=t.has_final_text,
                    error_count=t.error_count,
                    tool_calls=calls,
                    tool_success=succ,
                    tool_failure=fail,
                    recovered=rec["recovered"],
                    first_ts=t.first_ts,
                    last_ts=t.last_ts,
                )
            )
            total_errors += t.error_count
            total_success += succ
            total_failure += fail

        any_final = any(e.has_final_text for e in executions)
        any_recovered = any(e.recovered for e in executions)
        all_recovered_or_clean = all(
            e.recovered or e.error_count == 0 for e in executions
        )

        # completion: did the role deliver any output across its attempts?
        completion = CheckOutcome.pass_ if any_final else CheckOutcome.fail
        # error profile: bounded + (recovered or clean) → pass; errors but delivered → warn; dead → fail
        if total_errors == 0:
            error_profile = CheckOutcome.pass_
        elif all_recovered_or_clean and any_recovered:
            error_profile = CheckOutcome.pass_
        elif any_final:
            error_profile = CheckOutcome.warn
        else:
            error_profile = CheckOutcome.fail

        keypoints = [
            Keypoint(id="role.delivered", description="Role produced a deliverable (final text)",
                     outcome=CheckOutcome.pass_ if any_final else CheckOutcome.fail),
            Keypoint(id="role.errors_bounded", description=f"Tool errors bounded (<{_ERR_THRESH}/exec) & recovered",
                     outcome=(CheckOutcome.pass_ if total_errors <= _ERR_THRESH * len(executions)
                              and all_recovered_or_clean else CheckOutcome.warn)),
            Keypoint(id="role.depth_quality", description="Research depth & correctness of findings (subjective)",
                     type="subjective", outcome=CheckOutcome.pending),
        ]

        role_evals.append(
            RoleEval(
                role=role,
                executions=executions,
                completion=completion,
                error_profile=error_profile,
                step_count=sum(e.step_count for e in executions),
                total_errors=total_errors,
                tool_success=total_success,
                tool_failure=total_failure,
                churn=len(executions),
                tool_summary=dict(tool_hist),
                keypoints=keypoints,
            )
        )
    return role_evals
