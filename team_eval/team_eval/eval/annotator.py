"""Load / save human (or LLM) annotations for subjective checks.

Annotations are stored as ``annotations/<session_id>.json`` and merged into the
EvalReport on rebuild so subjective outcomes persist across report regeneration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def annotations_path(runs_dir: str | Path, session_id: str) -> Path:
    return Path(runs_dir) / "annotations" / f"{session_id}.json"


def load_annotations(runs_dir: str | Path, session_id: str) -> dict[str, Any]:
    p = annotations_path(runs_dir, session_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_annotations(runs_dir: str | Path, session_id: str, data: dict[str, Any]) -> Path:
    p = annotations_path(runs_dir, session_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
