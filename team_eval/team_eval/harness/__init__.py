"""Capture harness: run headless Claude team sessions and feed the pipeline."""

from team_eval.harness.dataset import list_sessions, register_session, summarize
from team_eval.harness.pipeline import process_session
from team_eval.harness.run_claude import capture, find_latest_team_session, run_team

__all__ = [
    "capture",
    "find_latest_team_session",
    "list_sessions",
    "process_session",
    "register_session",
    "run_team",
    "summarize",
]
