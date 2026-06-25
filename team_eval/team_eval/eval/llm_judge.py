"""cc-sdk LLM judge for subjective checks (OFF by default; explicit trigger).

Mirrors astroneval's harness judge: an independent ``claude-agent-sdk`` session
with a custom MCP tool that forces a structured verdict. The judge NEVER runs
automatically from the default pipeline — call ``judge_report`` explicitly
(needs the SDK + Claude auth + spends tokens).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from team_eval.eval.judge_prompts import (
    JUDGE_SERVER_NAME,
    JUDGE_TOOL_NAME,
    build_planning_dossier,
    build_role_dossier,
    build_synthesis_dossier,
    judge_prompt,
)
from team_eval.eval.models import (
    CheckOutcome,
    DimensionScore,
    EvalReport,
    Verdict,
)
from team_eval.graph.models import TeamGraph
from team_eval.parse.models import TeamSession

try:
    from claude_agent_sdk import (
        ClaudeAgentOptions,
        ClaudeSDKClient,
        create_sdk_mcp_server,
        tool,
    )
    from claude_agent_sdk.types import ResultMessage

    SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    SDK_AVAILABLE = False
    ResultMessage = None  # type: ignore[assignment]


@dataclass
class JudgeState:
    called: bool = False
    payload: dict[str, Any] | None = None
    error: str | None = None
    usage: dict[str, Any] | None = None
    cost_usd: float | None = None


def _create_judge_server(state: JudgeState):
    @tool(
        JUDGE_TOOL_NAME,
        "Submit the judge's structured verdict on team execution quality",
        {
            "passed": bool,
            "confidence": float,
            "reasoning": str,
            "strengths": list,
            "weaknesses": list,
            "suggestions": list,
            "dimension_scores": list,
        },
    )
    async def submit_verdict(args: dict[str, Any]) -> dict[str, Any]:
        state.called = True
        passed = args.get("passed")
        reasoning = args.get("reasoning")
        if not isinstance(passed, bool):
            state.error = "passed must be bool"
            raise ValueError(state.error)
        if not isinstance(reasoning, str) or not reasoning.strip():
            state.error = "reasoning must be non-empty"
            raise ValueError(state.error)
        state.payload = {
            "passed": passed,
            "confidence": args.get("confidence"),
            "reasoning": reasoning,
            "strengths": args.get("strengths") or [],
            "weaknesses": args.get("weaknesses") or [],
            "suggestions": args.get("suggestions") or [],
            "dimension_scores": args.get("dimension_scores") or [],
        }
        return {"content": [{"type": "text", "text": "verdict recorded"}]}

    return create_sdk_mcp_server(
        name=JUDGE_SERVER_NAME, version="1.0.0", tools=[submit_verdict]
    )


def _to_verdict(state: JudgeState, model: str | None) -> Verdict:
    if state.error:
        return Verdict(error=state.error, judge_model=model,
                       cost_usd=state.cost_usd)
    if not state.payload:
        return Verdict(error="judge did not call the verdict tool",
                       judge_model=model, cost_usd=state.cost_usd)
    p = state.payload
    dims = []
    for d in p.get("dimension_scores") or []:
        if isinstance(d, dict):
            dims.append(DimensionScore(
                id=str(d.get("id", "")), name=str(d.get("name", d.get("id", ""))),
                score=float(d["score"]) if isinstance(d.get("score"), (int, float)) else None,
                analysis=d.get("analysis"),
                suggestions=[str(s) for s in (d.get("suggestions") or [])],
            ))
    conf = p.get("confidence")
    return Verdict(
        passed=p.get("passed"),
        confidence=float(conf) if isinstance(conf, (int, float)) else None,
        reasoning=p.get("reasoning"),
        strengths=[str(s) for s in p.get("strengths") or []],
        weaknesses=[str(s) for s in p.get("weaknesses") or []],
        suggestions=[str(s) for s in p.get("suggestions") or []],
        dimension_scores=dims,
        judge_model=model,
        cost_usd=state.cost_usd,
    )


def _load_claude_settings_env(
    settings_path: str | Path | None = None,
) -> dict[str, str]:
    """Load the ``env`` block from the user's Claude ``settings.json``.

    claude-agent-sdk spawns the CLI in a subprocess that does NOT reliably
    inherit the user's configured provider — e.g. a custom
    ``ANTHROPIC_BASE_URL`` + ``ANTHROPIC_AUTH_TOKEN`` + ``ANTHROPIC_MODEL``
    (BigModel/GLM, a proxy, etc.). Without these the spawned judge hits the real
    Anthropic endpoint and fails with ``api_retry``. Passing the settings ``env``
    block through ``ClaudeAgentOptions(env=...)`` makes the judge session use the
    same provider as the user's interactive Claude Code.
    """
    path = Path(settings_path) if settings_path else Path.home() / ".claude" / "settings.json"
    override = os.environ.get("TEAM_EVAL_CLAUDE_SETTINGS")
    if override:
        path = Path(override)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    env = data.get("env") or {}
    return {str(k): str(v) for k, v in env.items() if isinstance(v, (str, int, float, bool))}


async def _run_judge(prompt: str, workdir: str, model: str | None) -> JudgeState:
    state = JudgeState()
    settings_env = _load_claude_settings_env()
    # Default to the user's configured base model (e.g. glm-5.2) rather than the
    # SDK's tier alias, which may map to a "<model>[1M]" name needing a beta.
    effective_model = model or settings_env.get("ANTHROPIC_MODEL")
    stderr_lines: list[str] = []
    kwargs: dict[str, Any] = {
        "allowed_tools": ["Read", f"mcp__{JUDGE_SERVER_NAME}__{JUDGE_TOOL_NAME}"],
        "permission_mode": "bypassPermissions",
        "mcp_servers": {JUDGE_SERVER_NAME: _create_judge_server(state)},
        "env": settings_env,
        "setting_sources": ["user"],
        "stderr": lambda line: stderr_lines.append(line),
    }
    if effective_model:
        kwargs["model"] = effective_model
    if workdir:
        kwargs["cwd"] = workdir
    options = ClaudeAgentOptions(**kwargs)
    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if ResultMessage is not None and isinstance(message, ResultMessage):
                    state.usage = getattr(message, "usage", None)
                    state.cost_usd = getattr(message, "total_cost_usd", None)
    except Exception as exc:  # noqa: BLE001 - surface any judge failure
        state.error = f"{type(exc).__name__}: {exc}"
    # Surface the SDK stderr so failures (api_retry, auth) are diagnosable
    # instead of the opaque "Check stderr output for details".
    if (state.error or not state.called) and stderr_lines:
        state.error = (state.error or "judge did not call the verdict tool") + (
            "\n--- SDK stderr (tail) ---\n" + "".join(stderr_lines[-30:])
        )
    return state


def run_judge(area: str, dossier: str, *, workdir: str = ".",
              model: str | None = None) -> Verdict:
    """Run one judge session synchronously and return a Verdict."""
    if not SDK_AVAILABLE:
        return Verdict(error="claude-agent-sdk not installed (pip install claude-agent-sdk)")
    prompt = judge_prompt(area, dossier)
    try:
        state = asyncio.run(_run_judge(prompt, workdir, model))
    except RuntimeError as exc:
        return Verdict(error=f"judge runtime error: {exc}")
    return _to_verdict(state, model)


def _outcome_from_verdict(v: Verdict) -> CheckOutcome:
    if v.error:
        return CheckOutcome.pending
    if v.passed:
        return CheckOutcome.pass_
    scores = [d.score for d in v.dimension_scores if isinstance(d.score, (int, float))]
    mean = sum(scores) / len(scores) if scores else 0
    return CheckOutcome.warn if mean >= 50 else CheckOutcome.fail


def _set_check(report: EvalReport, cid: str, v: Verdict) -> None:
    for c in report.checks:
        if c.id == cid:
            c.verdict = v
            c.outcome = _outcome_from_verdict(v)
            c.explanation = (v.reasoning or v.error or "")[:400]
            if v.dimension_scores:
                c.dimension_scores = v.dimension_scores
            return


def judge_report(
    report: EvalReport,
    session: TeamSession,
    graph: TeamGraph,
    *,
    workdir: str = ".",
    model: str | None = None,
    areas: tuple[str, ...] = ("synthesis", "planning", "role_depth"),
) -> EvalReport:
    """Fill subjective checks + role judgements via the cc-sdk judge."""
    if not SDK_AVAILABLE:
        raise RuntimeError("claude-agent-sdk not installed (pip install claude-agent-sdk)")

    if "synthesis" in areas:
        v = run_judge("synthesis", build_synthesis_dossier(session, graph),
                      workdir=workdir, model=model)
        _set_check(report, "O3", v)

    if "planning" in areas:
        v = run_judge("planning", build_planning_dossier(session, graph),
                      workdir=workdir, model=model)
        _set_check(report, "C5", v)

    role_outcomes: list[CheckOutcome] = []
    if "role_depth" in areas:
        for re_ in report.role_evals:
            v = run_judge("role_depth", build_role_dossier(session, re_.role),
                          workdir=workdir, model=model)
            re_.judgement = v
            oc = _outcome_from_verdict(v)
            role_outcomes.append(oc)
            for kp in re_.keypoints:
                if kp.id == "role.depth_quality":
                    kp.outcome = oc
            # map onto the owning task's quality keypoint
            for t in report.task_evals:
                if t.owner == re_.role:
                    for kp in t.keypoints:
                        if kp.id.endswith(".quality"):
                            kp.outcome = oc

    # O4 research depth = worst per-role depth outcome
    if role_outcomes:
        rank = {CheckOutcome.fail: 0, CheckOutcome.warn: 1, CheckOutcome.pending: 2,
                CheckOutcome.pass_: 3}
        worst = min(role_outcomes, key=lambda o: rank.get(o, 4))
        for c in report.checks:
            if c.id == "O4":
                c.outcome = worst
                c.explanation = "aggregated from per-role depth judgements"

    # rebuild conclusion + score now that subjective outcomes are filled
    from team_eval.eval.checks import build_conclusion, _score

    report.conclusion = build_conclusion(report.checks, report.role_evals, report.task_evals)
    report.score = _score(report.checks)
    return report
