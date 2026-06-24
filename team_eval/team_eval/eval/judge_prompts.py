"""Prompt + dossier builders for the cc-sdk LLM judge.

Each area (synthesis / planning / role-depth) builds a compact dossier from the
parsed session and a prompt that forces a structured verdict via the judge MCP
tool (mirrors astroneval's harness/prompts/judge.py pattern).
"""

from __future__ import annotations

from team_eval.graph.models import TeamGraph
from team_eval.parse.models import TeamSession

JUDGE_SERVER_NAME = "team_eval_judge"
JUDGE_TOOL_NAME = "submit_verdict"
JUDGE_TOOL_FULL_NAME = f"mcp__{JUDGE_SERVER_NAME}__{JUDGE_TOOL_NAME}"

_SYNTH = 2500
_PLANNING = 2000
_ROLE = 2500


def _original_task(session: TeamSession) -> str:
    for s in session.leader.steps:
        if s.source == "user" and s.text:
            return s.text[:600]
    return "(unknown)"


def _task_lines(session: TeamSession) -> str:
    if not session.tasks:
        return "(no tasks in shared list)"
    return "\n".join(
        f"- #{t.id} [{t.final_status}] owner={t.owner}: {t.subject}" for t in session.tasks
    )


def _role_finals(session: TeamSession) -> str:
    by_role: dict[str, list[str]] = {}
    for sub in session.subagents:
        by_role.setdefault(sub.role, []).append(sub.final_text or "(no final text)")
    lines = []
    for role, texts in by_role.items():
        # take the longest final text per role as the representative deliverable
        best = max(texts, key=len) if texts else ""
        lines.append(f"### {role}\n{best[:500]}")
    return "\n\n".join(lines)


def _leader_longest_text(session: TeamSession, n: int) -> str:
    best = ""
    for step in session.leader.steps:
        if step.source == "agent" and len(step.text) > len(best):
            best = step.text
    return best[:n]


def build_synthesis_dossier(session: TeamSession, graph: TeamGraph) -> str:
    return (
        f"## 原始任务\n{_original_task(session)}\n\n"
        f"## 共享任务清单\n{_task_lines(session)}\n\n"
        f"## Leader 最长文本（综合候选）\n{_leader_longest_text(session, _SYNTH)}\n\n"
        f"## 各角色代表产出（最长 final text）\n{_role_finals(session)}\n"
    )


def build_planning_dossier(session: TeamSession, graph: TeamGraph) -> str:
    roles = sorted({s.role for s in session.subagents})
    spawns = ", ".join(f"{sp.name}×1" for sp in session.spawns) or "(none)"
    return (
        f"## 原始任务\n{_original_task(session)}\n\n"
        f"## 拆解结果\n- 角色: {roles}\n- 角色数: {len(roles)}\n"
        f"- 派生(spawn): {spawns}\n- 模式: {graph.pattern}\n\n"
        f"## 任务→角色分配\n{_task_lines(session)}\n\n"
        f"## Leader 规划相关文本\n{_leader_longest_text(session, _PLANNING)}\n"
    )


def build_role_dossier(session: TeamSession, role: str) -> str:
    subs = [s for s in session.subagents if s.role == role]
    if not subs:
        return f"(no transcripts for role {role})"
    rep = max(subs, key=lambda t: t.step_count)
    tools = sorted(rep.tool_hist.items(), key=lambda x: -x[1])[:12]
    tool_lines = "\n".join(f"- {n}: {c}" for n, c in tools) or "(none)"
    # sample a few Read/Grep targets as evidence of depth
    ev = []
    for step in rep.steps:
        for tu in step.tool_uses:
            if tu.name in ("Read", "Grep", "Glob") and tu.arguments:
                p = tu.arguments.get("file_path") or tu.arguments.get("pattern") or tu.arguments.get("path")
                if p:
                    ev.append(f"{tu.name} {str(p)[:80]}")
    ev_lines = "\n".join(f"- {e}" for e in ev[:25]) or "(none)"
    return (
        f"## 角色: {role}  (执行次数={len(subs)}, 代表transcript步数={rep.step_count}, 错误={rep.error_count})\n"
        f"## 代表产出(final text)\n{(rep.final_text or '(none)')[:_ROLE]}\n\n"
        f"## 工具使用(top)\n{tool_lines}\n\n"
        f"## 调研证据(读/搜索目标样本)\n{ev_lines}\n"
    )


def judge_prompt(area: str, dossier: str) -> str:
    """Build the judge prompt for one subjective area."""
    if area == "synthesis":
        dims = ("- completeness: 是否覆盖了原始任务和所有子任务的关键发现\n"
                "- accuracy: 内容是否正确、有无臆造/幻觉\n"
                "- structure: 结构是否清晰、可读\n"
                "- actionability: 结论是否可落地")
        target = "Leader 的综合报告/最终产出"
    elif area == "planning":
        dims = ("- decomposition_quality: 任务拆解是否合理、是否覆盖原始任务全貌\n"
                "- focus: 是否聚焦、有无过度/不足拆解、有无冗余重叠\n"
                "- role_balance: 角色划分是否均衡、边界是否清晰\n"
                "- scoping: 每个任务的范围是否明确")
        target = "Leader 的任务拆解与角色规划"
    else:  # role_depth
        dims = ("- depth: 调研是否深入、是否触及真实代码/路径而非泛泛\n"
                "- correctness: 发现是否正确\n"
                "- evidence: 是否有可验证的证据(文件/函数/数据)\n"
                "- relevance: 是否切中所分配任务")
        target = "该角色的调研深度与质量"

    return f"""你是多智能体 Team 执行质量的独立评委（Judge）。请独立、严格地评判。

## 评判对象
{target}

## 评判材料
{dossier}

## 评分维度（每个给 0-100 分 + 简评）
{dims}

## 协议要求
分析完成后，你必须调用工具 `{JUDGE_TOOL_FULL_NAME}` 提交判定，且只调用一次。
参数：
- passed: bool（整体是否达标：综合/规划≥60 且无致命问题→true）
- confidence: 0-1（你对判定的把握）
- reasoning: 非空（判定理由，必须引用材料中的具体证据）
- strengths: 字符串数组（做得好的点）
- weaknesses: 字符串数组（问题点）
- suggestions: 字符串数组（改进建议）
- dimension_scores: 数组，每项 {{id, name, score(0-100), analysis, suggestions(数组)}}
  id 用维度简写：{"completeness/accuracy/structure/actionability" if area == "synthesis" else "decomposition_quality/focus/role_balance/scoping" if area == "planning" else "depth/correctness/evidence/relevance"}

未提供充分证据时，宁可给低分并说明。只有调用该工具才算完成判定。"""
