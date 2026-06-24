"""Accumulate captured team sessions into a queryable dataset index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _index_path(sessions_dir: str | Path) -> Path:
    return Path(sessions_dir) / "index.json"


def list_sessions(sessions_dir: str | Path) -> list[dict[str, Any]]:
    p = _index_path(sessions_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("sessions", []) if isinstance(data, dict) else data
    except (OSError, json.JSONDecodeError):
        return []


def register_session(
    sessions_dir: str | Path,
    *,
    session_id: str,
    team_name: str | None,
    pattern: str | None,
    health: float | None,
    score_counts: dict[str, Any] | None,
    source_path: str,
    captured_at: str,
) -> Path:
    """Insert or update a session entry in the dataset index."""
    p = _index_path(sessions_dir)
    Path(sessions_dir).mkdir(parents=True, exist_ok=True)
    sessions = list_sessions(sessions_dir)
    entry = {
        "session_id": session_id,
        "team_name": team_name,
        "pattern": pattern,
        "health": health,
        "score_counts": score_counts,
        "source_path": source_path,
        "captured_at": captured_at,
    }
    sessions = [e for e in sessions if e.get("session_id") != session_id]
    sessions.append(entry)
    sessions.sort(key=lambda e: e.get("captured_at", ""))
    p.write_text(
        json.dumps({"sessions": sessions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return p


def summarize(sessions_dir: str | Path) -> dict[str, Any]:
    """Cross-session aggregate for metric iteration (means, distributions)."""
    sessions = list_sessions(sessions_dir)
    n = len(sessions)
    healths = [s["health"] for s in sessions if isinstance(s.get("health"), (int, float))]
    patterns: dict[str, int] = {}
    for s in sessions:
        p = s.get("pattern") or "unknown"
        patterns[p] = patterns.get(p, 0) + 1
    return {
        "session_count": n,
        "mean_health": round(sum(healths) / len(healths), 1) if healths else None,
        "min_health": min(healths) if healths else None,
        "max_health": max(healths) if healths else None,
        "pattern_distribution": patterns,
    }
