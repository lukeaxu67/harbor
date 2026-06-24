"""Capture tool: run a headless Claude Team session and locate its transcript.

Explicitly invoked by the user (it spends tokens and needs the Claude CLI + API
key). It does NOT run automatically from the main pipeline.

Usage:
    python -m team_eval.harness.run_claude --prompt "..." [--workdir DIR] \\
        [--runs runs] [--sessions sessions] [--timeout 1800]
    python -m team_eval.harness.run_claude --prompt-file prompt.txt
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_PROMPT = (
    "使用 Team 模式（多智能体团队）完成一个调研项目：组建一个包含若干 researcher "
    "的团队，分别调研某开源项目的不同方面（架构、CLI、agent/环境后端、benchmark/verifier、"
    "infra/CI），最后由 leader 汇总成一份报告。"
)


def find_claude() -> str | None:
    for name in ("claude", "claude.exe", "claude.cmd"):
        p = shutil.which(name)
        if p:
            return p
    return None


def projects_root() -> Path:
    return Path.home() / ".claude" / "projects"


def find_latest_team_session(after: float | None = None) -> Path | None:
    """Newest *.jsonl under ~/.claude/projects that has a sibling subagents/ dir."""
    root = projects_root()
    if not root.exists():
        return None
    candidates: list[Path] = []
    for jsonl in root.rglob("*.jsonl"):
        if "subagents" in jsonl.parts:
            continue
        stem_dir = jsonl.parent / jsonl.stem
        if not (stem_dir / "subagents").is_dir():
            continue  # only team sessions
        if after is not None and jsonl.stat().st_mtime < after:
            continue
        candidates.append(jsonl)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run_team(
    prompt: str,
    *,
    workdir: str | Path = ".",
    timeout: int = 1800,
    model: str | None = None,
    extra_args: list[str] | None = None,
) -> tuple[int, str, str]:
    """Invoke the Claude CLI headlessly. Returns (returncode, stdout, stderr)."""
    claude = find_claude()
    if not claude:
        raise RuntimeError(
            "claude CLI not found on PATH. Install Claude Code and ensure `claude` is callable."
        )
    cmd = [claude, "-p", prompt, "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]
    if extra_args:
        cmd += extra_args
    proc = subprocess.run(  # noqa: S603 - user-invoked, user-controlled prompt
        cmd,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def capture(
    prompt: str,
    *,
    workdir: str | Path = ".",
    runs_dir: str | Path = "runs",
    sessions_dir: str | Path = "sessions",
    timeout: int = 1800,
    model: str | None = None,
    register: bool = True,
) -> dict:
    """Run a team session and process it through the full pipeline."""
    from team_eval.harness.pipeline import process_session

    start = time.time()
    rc, out, err = run_team(prompt, workdir=workdir, timeout=timeout, model=model)
    session_path = find_latest_team_session(after=start - 5)
    if session_path is None:
        raise RuntimeError(
            f"No team session captured (claude rc={rc}). stderr:\n{err[:2000]}"
        )
    captured_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    result = process_session(
        session_path,
        runs_dir=runs_dir,
        sessions_dir=sessions_dir if register else None,
        register=register,
        captured_at=captured_at,
    )
    result["claude_rc"] = rc
    result["claude_stderr"] = err
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="team_eval.harness.run_claude", description=__doc__)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--prompt", default=DEFAULT_PROMPT)
    g.add_argument("--prompt-file")
    parser.add_argument("--workdir", default=".")
    parser.add_argument("--runs", default="runs")
    parser.add_argument("--sessions", default="sessions")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--model", default=None)
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args(argv)

    prompt = args.prompt
    if args.prompt_file:
        prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    print(f"[harness] launching claude team run (workdir={args.workdir})…")
    res = capture(
        prompt,
        workdir=args.workdir,
        runs_dir=args.runs,
        sessions_dir=args.sessions,
        timeout=args.timeout,
        model=args.model,
        register=not args.no_register,
    )
    print(f"[harness] session={res['session_id']} team={res['team_name']} "
          f"health={res['report'].score.get('health')} atif_valid={res['atif_valid']}")
    print(f"[harness] report → {res['html_path']}")
    if res.get("claude_rc") not in (0, None):
        print(f"[harness] warning: claude rc={res['claude_rc']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
