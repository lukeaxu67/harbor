"""Checkpoint catalog (metadata + objective evaluators) and report builder.

Each objective check carries an ``evaluate(ctx)`` that returns
``(outcome, explanation, evidence, metric)``. Subjective checks carry a rubric
and stay ``pending`` until annotated (see annotator.py / llm_judge.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from team_eval.eval.models import (
    AngleConclusion,
    CheckEvidence,
    CheckKind,
    CheckOutcome,
    CheckPoint,
    EvalReport,
    RoleEval,
    Severity,
    TaskEval,
)
from team_eval.eval.role_eval import build_role_evals
from team_eval.eval.stats import compute_stats
from team_eval.eval.task_eval import build_task_evals
from team_eval.graph.models import TeamGraph
from team_eval.parse.models import TeamSession

MAX_STEPS = 200        # per-transcript step blow-up threshold
ERR_THRESH = 10        # per-agent tool-error threshold
COVERAGE_MIN_LEN = 200  # min chars for a leader turn to count as "substantial"

DomainKeywords = {
    "arch-researcher": ["architecture", "core", "lifecycle", "abstraction", "orchestrat"],
    "cli-researcher": ["cli", "command", "typer", "harbor ", "subcommand"],
    "agents-env-researcher": ["agent", "environment", "backend", "docker", "daytona"],
    "benchmarks-researcher": ["benchmark", "verifier", "metric", "registry", "dataset"],
    "infra-researcher": ["storage", "package", "publish", "ci", "supabase", "viewer", "web"],
}

_IDLE_MARKERS = ("idle", "standing by", "no action needed", "awaiting", "holding for",
                 "nothing's changed", "nothing has changed")


@dataclass
class EvalContext:
    session: TeamSession
    graph: TeamGraph
    stats: dict[str, Any]
    atif: dict[str, Any]


CheckResult = tuple[CheckOutcome, str, list[CheckEvidence], dict[str, Any] | None]
Evaluator = Callable[[EvalContext], CheckResult]


@dataclass
class CheckDef:
    id: str
    dimension: str
    title: str
    description: str
    kind: CheckKind
    severity: Severity = Severity.minor
    tags: list[str] = field(default_factory=list)
    rubric: str | None = None
    evaluate: Evaluator | None = None  # set for objective checks

    @property
    def auto(self) -> bool:
        return self.kind == CheckKind.objective


# --------------------------------------------------------------------------- #
# objective evaluators
# --------------------------------------------------------------------------- #

def _ev_team_created(ctx: EvalContext) -> CheckResult:
    ok = (ctx.session.counts.get("TeamCreate", 0) >= 1) and bool(ctx.session.team_name)
    ev = [CheckEvidence(ref_kind="team", snippet=f"team_name={ctx.session.team_name}")]
    return (
        CheckOutcome.pass_ if ok else CheckOutcome.fail,
        f"Team created via TeamCreate (team_name={ctx.session.team_name!r})",
        ev,
        {"team_name": ctx.session.team_name},
    )


def _ev_roles_have_transcripts(ctx: EvalContext) -> CheckResult:
    spawned = {sp.name for sp in ctx.session.spawns if sp.name}
    have = {s.role for s in ctx.session.subagents}
    missing = sorted(spawned - have)
    ok = not missing
    return (
        CheckOutcome.pass_ if ok else CheckOutcome.fail,
        f"{len(have)} role(s) have transcripts; spawned={sorted(spawned)}",
        [CheckEvidence(role=r, ref_kind="spawn") for r in missing],
        {"spawned_roles": sorted(spawned), "roles_with_transcripts": sorted(have)},
    )


def _ev_valid_roletypes(ctx: EvalContext) -> CheckResult:
    roles = {s.role for s in ctx.session.subagents}
    bad = [r for r in roles if not r or r == "unknown"]
    return (
        CheckOutcome.pass_ if not bad else CheckOutcome.fail,
        f"role types: {sorted(roles)}",
        [CheckEvidence(role=r) for r in bad],
        {"roles": sorted(roles)},
    )


def _ev_teammate_final_text(ctx: EvalContext) -> CheckResult:
    missing = [s.role for s in ctx.session.subagents if not s.has_final_text]
    return (
        CheckOutcome.pass_ if not missing else CheckOutcome.warn,
        f"{len(ctx.session.subagents) - len(missing)}/{len(ctx.session.subagents)} "
        f"teammates produced a final text turn",
        [CheckEvidence(role=r, ref_kind="final_text") for r in missing],
        {"missing_final_text": missing},
    )


def _ev_no_step_blowup(ctx: EvalContext) -> CheckResult:
    worst = max(
        [ctx.session.leader, *ctx.session.subagents],
        key=lambda t: t.step_count,
        default=None,
    )
    n = worst.step_count if worst else 0
    outcome = CheckOutcome.pass_ if n <= MAX_STEPS else CheckOutcome.warn
    return (
        outcome,
        f"max transcript steps = {n} (threshold {MAX_STEPS}) on "
        f"{worst.role if worst else 'n/a'}",
        [CheckEvidence(role=worst.role, agent_id=worst.agent_id)] if worst else [],
        {"max_steps": n, "threshold": MAX_STEPS},
    )


def _ev_tool_errors(ctx: EvalContext) -> CheckResult:
    by_role = {s.role: s.error_count for s in ctx.session.subagents}
    flagged = {r: c for r, c in by_role.items() if c > ERR_THRESH}
    total = sum(by_role.values())
    if not flagged:
        outcome = CheckOutcome.pass_ if total == 0 else CheckOutcome.warn
    else:
        outcome = CheckOutcome.fail
    return (
        outcome,
        f"tool errors: {total} total; per-role={by_role}; threshold={ERR_THRESH}/agent",
        [CheckEvidence(role=r, ref_kind="error", snippet=f"{c} errors") for r, c in flagged.items()],
        {"total": total, "per_role": by_role, "threshold": ERR_THRESH},
    )


def _ev_leader_final_text(ctx: EvalContext) -> CheckResult:
    ok = ctx.session.leader.has_final_text
    return (
        CheckOutcome.pass_ if ok else CheckOutcome.warn,
        "leader produced a final text turn" if ok else "no leader final text turn",
        [],
        None,
    )


def _ev_task_closure(ctx: EvalContext) -> CheckResult:
    tasks = ctx.session.tasks
    completed = sum(1 for t in tasks if t.final_status == "completed")
    deleted = sum(1 for t in tasks if t.final_status == "deleted")
    total = len(tasks)
    if total == 0:
        return CheckOutcome.na, "no tasks in shared list", [], None
    ok = completed == total
    outcome = CheckOutcome.pass_ if ok else CheckOutcome.fail
    return (
        outcome,
        f"task closure: {completed}/{total} completed, {deleted} deleted",
        [CheckEvidence(ref_kind="task", snippet=f"#{t.id} {t.final_status}: {t.subject[:40]}")
         for t in tasks if t.final_status != "completed"],
        {"completed": completed, "deleted": deleted, "total": total},
    )


def _ev_no_orphan_messages(ctx: EvalContext) -> CheckResult:
    orphans = ctx.graph.orphan_targets
    return (
        CheckOutcome.pass_ if not orphans else CheckOutcome.warn,
        f"orphan message/spawn targets: {orphans}" if orphans else "no orphan targets",
        [CheckEvidence(role=r, ref_kind="orphan") for r in orphans],
        {"orphans": orphans},
    )


def _ev_sendmessage_errors(ctx: EvalContext) -> CheckResult:
    errs = ctx.session.sendmessage_errors
    outcome = CheckOutcome.pass_ if not errs else CheckOutcome.fail
    return (
        outcome,
        f"{len(errs)} SendMessage error(s) (e.g. missing 'summary')",
        [CheckEvidence(step_id=e.get("step_id"), role=e.get("to"),
                       ref_kind="message-error", snippet=str(e.get("error"))[:80])
         for e in errs[:8]],
        {"count": len(errs)},
    )


def _ev_clean_teamdelete(ctx: EvalContext) -> CheckResult:
    n = ctx.session.teamdelete_count
    if n == 0:
        outcome, msg = CheckOutcome.warn, "TeamDelete never called (no cleanup)"
    elif n == 1:
        outcome, msg = CheckOutcome.pass_, "TeamDelete called once (clean cleanup)"
    else:
        outcome, msg = CheckOutcome.warn, f"TeamDelete called {n} times (shutdown churn)"
    return outcome, msg, [], {"count": n}


def _ev_synthesis_present(ctx: EvalContext) -> CheckResult:
    rq = ctx.graph.reduce_quality
    outcome = {"complete": CheckOutcome.pass_,
               "partial": CheckOutcome.warn,
               "none": CheckOutcome.fail}.get(rq, CheckOutcome.warn)
    return (
        outcome,
        f"reduce/synthesis quality = {rq}",
        [],
        {"reduce_quality": rq},
    )


def _leader_substantial_text(session: TeamSession) -> str:
    best = ""
    for step in session.leader.steps:
        if step.source == "agent" and len(step.text) > len(best):
            best = step.text
    return best


def _ev_domain_coverage(ctx: EvalContext) -> CheckResult:
    synthesis = _leader_substantial_text(ctx.session)
    low = synthesis.lower()
    covered, missing = [], []
    for role, kws in DomainKeywords.items():
        if any(k in low for k in kws):
            covered.append(role)
        else:
            missing.append(role)
    if len(synthesis) < COVERAGE_MIN_LEN:
        outcome = CheckOutcome.fail
        note = "no substantial leader synthesis text found"
    else:
        ratio = len(covered) / len(DomainKeywords)
        outcome = (CheckOutcome.pass_ if ratio == 1.0
                   else CheckOutcome.warn if ratio >= 0.6 else CheckOutcome.fail)
        note = f"coverage {len(covered)}/{len(DomainKeywords)} in longest leader turn"
    return (
        outcome,
        note,
        [CheckEvidence(role=r, ref_kind="coverage") for r in missing],
        {"covered": covered, "missing": missing,
         "synthesis_len": len(synthesis)},
    )


def _ev_total_tokens(ctx: EvalContext) -> CheckResult:
    t = ctx.stats["totals"]["tokens"]
    return (
        CheckOutcome.pass_,
        f"total tokens (prompt+completion+cached): "
        f"{t['all']:,} (prompt {t['prompt']:,}, completion {t['completion']:,}, "
        f"cached {t['cached']:,})",
        [],
        t,
    )


def _ev_parallelism(ctx: EvalContext) -> CheckResult:
    par = ctx.stats["worker_parallel"]
    return (
        CheckOutcome.pass_ if par else CheckOutcome.warn,
        "workers ran with overlapping active windows" if par
        else "workers ran sequentially (low parallelism)",
        [],
        {"worker_parallel": par},
    )


def _ev_churn(ctx: EvalContext) -> CheckResult:
    mx = ctx.stats["churn"]["max"]
    outcome = (CheckOutcome.pass_ if mx <= 2
               else CheckOutcome.warn if mx == 3 else CheckOutcome.fail)
    return (
        outcome,
        f"max transcripts per role = {mx} ({ctx.stats['churn']['by_role']})",
        [],
        {"max": mx, "by_role": ctx.stats["churn"]["by_role"]},
    )


def _ev_intervention(ctx: EvalContext) -> CheckResult:
    stops = ctx.session.taskstop_targets
    td = ctx.session.teamdelete_count
    flagged = bool(stops) or td > 1
    return (
        CheckOutcome.warn if flagged else CheckOutcome.pass_,
        f"manual interventions: TaskStop={stops}, TeamDelete={td}",
        [CheckEvidence(ref_kind="taskstop", snippet=t) for t in stops],
        {"taskstop": stops, "teamdelete": td},
    )


def _ev_clean_termination(ctx: EvalContext) -> CheckResult:
    final = ctx.session.leader.final_text or ""
    low = final.lower()
    idle = any(m in low for m in _IDLE_MARKERS)
    tasks_ok = all(t.final_status == "completed" for t in ctx.session.tasks) if ctx.session.tasks else False
    clean = (not idle) and tasks_ok and ctx.session.teamdelete_count == 1
    if clean:
        outcome, msg = CheckOutcome.pass_, "session terminated cleanly"
    else:
        reasons = []
        if idle:
            reasons.append("leader ended on an idle/standby message")
        if not tasks_ok:
            reasons.append(f"tasks not all completed "
                           f"({sum(1 for t in ctx.session.tasks if t.final_status=='completed')}"
                           f"/{len(ctx.session.tasks)})")
        if ctx.session.teamdelete_count != 1:
            reasons.append(f"TeamDelete called {ctx.session.teamdelete_count}x")
        outcome = CheckOutcome.fail
        msg = "unclean termination: " + "; ".join(reasons)
    return (
        outcome,
        msg,
        [CheckEvidence(role="team-lead", ref_kind="termination",
                       snippet=final[:160])],
        {"idle_end": idle, "tasks_completed": tasks_ok},
    )


def _ev_error_rate(ctx: EvalContext) -> CheckResult:
    errs = ctx.stats["totals"]["errors"]
    calls = ctx.stats["totals"]["tool_calls"] or 1
    rate = errs / calls
    outcome = (CheckOutcome.pass_ if rate < 0.05
               else CheckOutcome.warn if rate < 0.15 else CheckOutcome.fail)
    return (
        outcome,
        f"error rate = {errs}/{calls} = {rate:.1%}",
        [],
        {"errors": errs, "tool_calls": calls, "rate": round(rate, 4)},
    )


def _atif_check(ctx: EvalContext, prefix: str) -> tuple[bool, str]:
    for c in ctx.atif.get("checks", []):
        if str(c.get("id", "")).startswith(prefix):
            return bool(c.get("passed")), str(c.get("detail"))
    return False, "not reported"


def _ev_atif_pyDantic(ctx: EvalContext) -> CheckResult:
    ok, detail = _atif_check(ctx, "A1.")
    return (CheckOutcome.pass_ if ok else CheckOutcome.fail, detail, [], None)


def _ev_atif_schema(ctx: EvalContext) -> CheckResult:
    parts = []
    allok = True
    for prefix in ("A2.unique", "A2.sequential", "A2.source"):
        ok, detail = _atif_check(ctx, prefix)
        allok = allok and ok
        parts.append(detail)
    return (CheckOutcome.pass_ if allok else CheckOutcome.fail,
            "; ".join(parts), [], None)


def _ev_atif_refs(ctx: EvalContext) -> CheckResult:
    ok, detail = _atif_check(ctx, "A3.")
    return (CheckOutcome.pass_ if ok else CheckOutcome.fail, detail, [], None)


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #

def _o(cid, dim, title, desc, ev, *, sev=Severity.minor, tags=None):
    return CheckDef(cid, dim, title, desc, CheckKind.objective, sev, tags or [], None, ev)


def _s(cid, dim, title, desc, rubric, *, sev=Severity.major, tags=None):
    return CheckDef(cid, dim, title, desc, CheckKind.subjective, sev, tags or [], rubric, None)


CHECKS: list[CheckDef] = [
    # structural
    _o("S1", "structural", "Team created", "TeamCreate called with a team name.", _ev_team_created, sev=Severity.critical, tags=["team", "leader"]),
    _o("S2", "structural", "Every spawned role has a transcript", "Each Agent-spawned role produced >=1 subagent transcript.", _ev_roles_have_transcripts, sev=Severity.major, tags=["spawn", "churn"]),
    _o("S3", "structural", "Valid role types", "All subagents carry a non-empty agentType.", _ev_valid_roletypes, sev=Severity.minor, tags=["role"]),
    # execution
    _o("E1", "execution", "Teammates produced final text", "Every teammate has a final agent text turn.", _ev_teammate_final_text, sev=Severity.minor, tags=["output"]),
    _o("E2", "execution", "No transcript step blow-up", f"No transcript exceeds {MAX_STEPS} steps.", _ev_no_step_blowup, sev=Severity.minor, tags=["steps"]),
    _o("E3", "execution", "Per-agent tool errors bounded", f"No agent exceeds {ERR_THRESH} tool errors.", _ev_tool_errors, sev=Severity.major, tags=["error"]),
    _o("E4", "execution", "Leader final text present", "Leader produced a final text turn.", _ev_leader_final_text, sev=Severity.info, tags=["leader"]),
    # coordination
    _o("C1", "coordination", "Task closure", "All shared-list tasks reached 'completed'.", _ev_task_closure, sev=Severity.critical, tags=["task-lifecycle"]),
    _o("C2", "coordination", "No orphan messages/spawns", "Every message/spawn target resolves to a node.", _ev_no_orphan_messages, sev=Severity.minor, tags=["message"]),
    _o("C3", "coordination", "No SendMessage errors", "SendMessage calls did not error.", _ev_sendmessage_errors, sev=Severity.major, tags=["message-error"]),
    _o("C4", "coordination", "Clean team cleanup", "TeamDelete called exactly once.", _ev_clean_teamdelete, sev=Severity.minor, tags=["shutdown"]),
    _s("C5", "coordination", "Leader delegation discipline", "Leader delegated rather than doing the work itself.",
       "Does the leader avoid doing research/reading/writing itself and instead delegate to teammates? "
       "pass = leader only orchestrates; warn = minor self-work; fail = leader did core work itself.", tags=["leader", "delegation"]),
    # outcome
    _o("O1", "outcome", "Synthesis present", "A reduce/synthesis step was produced.", _ev_synthesis_present, sev=Severity.major, tags=["reduce"]),
    _o("O2", "outcome", "Domain coverage in synthesis", "Synthesis references all tasked domains.", _ev_domain_coverage, sev=Severity.major, tags=["reduce", "coverage"]),
    _s("O3", "outcome", "Synthesis quality", "Quality of the final synthesis.",
       "Structure, cites teammate findings, no hallucination, actionable. pass = strong; warn = thin; fail = absent/bad.", sev=Severity.critical, tags=["reduce", "quality"]),
    _s("O4", "outcome", "Research depth per domain", "Depth and correctness of each domain's findings.",
       "Per-role: did the researcher go beyond surface reading, cite real code/paths, answer the assigned question? pass/warn/fail.", sev=Severity.major, tags=["depth"]),
    # efficiency
    _o("F1", "efficiency", "Token cost reported", "Total token usage is reported (informational).", _ev_total_tokens, sev=Severity.info, tags=["token"]),
    _o("F2", "efficiency", "Worker parallelism", "Workers ran in overlapping windows.", _ev_parallelism, sev=Severity.minor, tags=["parallel"]),
    _o("F3", "efficiency", "Low transcript churn", "Few transcripts per role (low retry churn).", _ev_churn, sev=Severity.major, tags=["churn"]),
    # robustness
    _o("R1", "robustness", "Low error rate", "Tool error rate is low.", _ev_error_rate, sev=Severity.major, tags=["error"]),
    _o("R2", "robustness", "No manual intervention", "No TaskStop / repeated TeamDelete.", _ev_intervention, sev=Severity.minor, tags=["intervention"]),
    _o("R3", "robustness", "Clean termination", "Session ended cleanly (not idle, tasks done, 1 cleanup).", _ev_clean_termination, sev=Severity.critical, tags=["termination"]),
    # atif
    _o("A1", "atif", "ATIF Pydantic validity", "Trajectory validates via harbor models.", _ev_atif_pyDantic, sev=Severity.info, tags=["atif"]),
    _o("A2", "atif", "ATIF schema integrity", "Unique trajectory_ids, sequential step_ids, source_call_id integrity.", _ev_atif_schema, sev=Severity.info, tags=["atif"]),
    _o("A3", "atif", "ATIF refs resolvable", "SubagentTrajectoryRefs resolve to embedded trajectories.", _ev_atif_refs, sev=Severity.info, tags=["atif"]),
]

DIMENSIONS = ["structural", "execution", "coordination", "outcome",
              "efficiency", "robustness", "atif"]

# The 7 universal evaluation angles and the question each answers. Each angle
# aggregates a set of check dimensions (and optionally role/task signals).
ANGLES = [
    {"angle": "goal", "question": "最开始的任务完成了吗？",
     "dimensions": ["outcome"]},
    {"angle": "planning", "question": "拆解合理吗？聚焦吗？角色边界清晰吗？(领导力)",
     "dimensions": ["structural"]},
    {"angle": "delegation", "question": "活派对人了吗？交接干净吗？协调有效吗？",
     "dimensions": ["coordination"]},
    {"angle": "execution", "question": "每个角色完成得怎么样？谁强谁弱？",
     "dimensions": ["execution"]},
    {"angle": "robustness", "question": "中途出错了吗？发现并纠正了吗？(韧性/纠错)",
     "dimensions": ["robustness"]},
    {"angle": "efficiency", "question": "值得这个成本吗？(token/时间/并行/冗余)",
     "dimensions": ["efficiency"]},
    {"angle": "conformance", "question": "轨迹可复用/合规吗？(ATIF)",
     "dimensions": ["atif"]},
]

_OUTCOME_RANK = {CheckOutcome.fail: 0, CheckOutcome.warn: 1,
                 CheckOutcome.na: 2, CheckOutcome.pass_: 3, CheckOutcome.pending: 4}


def _worst_outcome(outcomes: list[CheckOutcome]) -> CheckOutcome:
    """Aggregate a set of outcomes into the worst (fail > warn > na > pass)."""
    resolved = [o for o in outcomes if o not in (None, CheckOutcome.pending)]
    if not resolved:
        return CheckOutcome.pending
    return min(resolved, key=lambda o: _OUTCOME_RANK.get(o, 4))


def _score(checks: list[CheckPoint]) -> dict[str, Any]:
    counts = {"pass": 0, "warn": 0, "fail": 0, "not_applicable": 0, "pending": 0}
    weight = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
    scored = 0.0
    n = 0
    for c in checks:
        o = c.outcome
        if o is None:
            continue
        key = o.value if isinstance(o, CheckOutcome) else str(o)
        counts[key] = counts.get(key, 0) + 1
        if key in weight:
            scored += weight[key]
            n += 1
    health = round(100 * scored / n, 1) if n else None
    return {"counts": counts, "health": health, "scored": n}


def build_conclusion(
    checks: list[CheckPoint],
    role_evals: list[RoleEval],
    task_evals: list[TaskEval],
) -> list[AngleConclusion]:
    """Roll checks + role/task evals up into the 7 universal-angle conclusions."""
    by_dim: dict[str, list[CheckPoint]] = {}
    for c in checks:
        by_dim.setdefault(c.dimension, []).append(c)
    out: list[AngleConclusion] = []
    for ang in ANGLES:
        angle = ang["angle"]
        rel = [c for d in ang["dimensions"] for c in by_dim.get(d, [])]
        outcomes = [c.outcome for c in rel if c.outcome]
        summaries = [f"{c.id}={c.outcome.value if c.outcome else '?'}: {c.explanation}"
                     for c in rel if c.explanation]

        # angle-specific extra signals
        if angle == "execution":
            outcomes += [re_.completion for re_ in role_evals]
            outcomes += [re_.error_profile for re_ in role_evals]
            weak = [re_.role for re_ in role_evals
                    if re_.error_profile == CheckOutcome.fail or re_.completion == CheckOutcome.fail]
            if weak:
                summaries.append(f"weak roles: {weak}")
        elif angle == "goal":
            outcomes += [t.completion for t in task_evals]
            done = sum(1 for t in task_evals if t.completion == CheckOutcome.pass_)
            summaries.append(f"tasks closed: {done}/{len(task_evals)}")
        elif angle == "robustness":
            recovered = sum(1 for re_ in role_evals
                            for e in re_.executions if e.recovered)
            dead = sum(1 for re_ in role_evals
                       for e in re_.executions
                       if e.error_count > 0 and not e.recovered)
            summaries.append(f"recovered executions: {recovered}; unrecovered: {dead}")

        verdict = _worst_outcome([o for o in outcomes if o is not None])
        out.append(AngleConclusion(
            angle=angle,
            question=ang["question"],
            verdict=verdict,
            summary=" | ".join(summaries[:6]),
        ))
    return out


def build_eval_report(
    session: TeamSession,
    graph: TeamGraph,
    atif_validation: dict[str, Any],
    *,
    annotations: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> EvalReport:
    """Run all objective checks and assemble an EvalReport."""
    stats = compute_stats(session, graph)
    ctx = EvalContext(session=session, graph=graph, stats=stats, atif=atif_validation)
    annotations = annotations or {}

    checks: list[CheckPoint] = []
    for cdef in CHECKS:
        if cdef.kind == CheckKind.objective and cdef.evaluate is not None:
            outcome, explanation, evidence, metric = cdef.evaluate(ctx)
            cp = CheckPoint(
                id=cdef.id, dimension=cdef.dimension, title=cdef.title,
                description=cdef.description, kind=cdef.kind, severity=cdef.severity,
                tags=cdef.tags, auto=True, outcome=outcome, explanation=explanation,
                evidence=evidence, metric=metric,
            )
        else:
            ann = annotations.get(cdef.id)
            outcome = CheckOutcome(ann["outcome"]) if ann and ann.get("outcome") else CheckOutcome.pending
            cp = CheckPoint(
                id=cdef.id, dimension=cdef.dimension, title=cdef.title,
                description=cdef.description, kind=cdef.kind, severity=cdef.severity,
                tags=cdef.tags, auto=False, outcome=outcome,
                explanation=ann.get("explanation") if ann else None,
                evidence=[CheckEvidence(**e) for e in (ann.get("evidence") or [])] if ann else [],
                rubric=cdef.rubric,
            )
        checks.append(cp)

    role_evals = build_role_evals(session)
    task_evals = build_task_evals(session, role_evals)
    conclusion = build_conclusion(checks, role_evals, task_evals)

    return EvalReport(
        session_id=session.session_id,
        team_name=session.team_name,
        pattern=graph.pattern,
        reduce_quality=graph.reduce_quality,
        checks=checks,
        role_evals=role_evals,
        task_evals=task_evals,
        conclusion=conclusion,
        stats=stats,
        score=_score(checks),
        generated_at=generated_at,
        notes=session.notes,
    )
