"""Validate a produced ATIF Trajectory against harbor's Pydantic models + self-checks."""

from __future__ import annotations

from typing import Any

from harbor.models.trajectories import Trajectory


def validate_atif(traj: Trajectory) -> dict[str, Any]:
    """Round-trip validate + run ATIF self-consistency checks.

    Returns a dict with ``valid`` (bool), ``errors`` (list[str]) and ``checks``
    (list of {id, passed, detail}).
    """
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    # 1) Pydantic round-trip (extra="forbid" + all model validators).
    try:
        data = traj.to_json_dict(exclude_none=True)
        Trajectory.model_validate(data)
        checks.append({"id": "A1.pydantic_roundtrip", "passed": True,
                       "detail": "Trajectory validates via harbor Pydantic models"})
    except Exception as exc:  # noqa: BLE001 - report any validation failure
        checks.append({"id": "A1.pydantic_roundtrip", "passed": False,
                       "detail": f"{type(exc).__name__}: {exc}"})
        errors.append(f"A1: {exc}")

    # 2) unique embedded trajectory_id
    embedded = traj.subagent_trajectories or []
    tids = [t.trajectory_id for t in embedded if t.trajectory_id]
    dupes = {t for t in tids if tids.count(t) > 1}
    checks.append({"id": "A2.unique_trajectory_ids", "passed": not dupes,
                   "detail": f"{len(set(tids))} unique / {len(tids)} embedded"
                             + (f"; duplicates: {dupes}" if dupes else "")})

    # 3) sequential step_ids from 1 (root + each embedded)
    bad_seq = []
    for label, t in [("root", traj)] + [(f"sub:{t.trajectory_id}", t) for t in embedded]:
        for i, step in enumerate(t.steps, start=1):
            if step.step_id != i:
                bad_seq.append(f"{label}@{i}")
                break
    checks.append({"id": "A2.sequential_step_ids", "passed": not bad_seq,
                   "detail": "all trajectories sequential from 1" if not bad_seq
                             else f"non-sequential: {bad_seq}"})

    # 4) observation source_call_id integrity
    dangling = 0
    for t in [traj, *embedded]:
        for step in t.steps:
            if not step.observation:
                continue
            ids = {tc.tool_call_id for tc in (step.tool_calls or [])}
            for r in step.observation.results:
                if r.source_call_id and r.source_call_id not in ids:
                    dangling += 1
    checks.append({"id": "A2.source_call_id_integrity", "passed": dangling == 0,
                   "detail": f"{dangling} dangling source_call_id reference(s)"})

    # 5) SubagentTrajectoryRef resolvability
    resolvable_ids = {t.trajectory_id for t in embedded}
    unresolved = []
    for step in traj.steps:
        if not step.observation:
            continue
        for r in step.observation.results:
            for ref in (r.subagent_trajectory_ref or []):
                if ref.trajectory_id and ref.trajectory_id not in resolvable_ids:
                    unresolved.append(ref.trajectory_id)
    checks.append({"id": "A3.ref_resolvable", "passed": not unresolved,
                   "detail": f"{len(resolvable_ids)} embedded; {len(unresolved)} unresolved ref(s)"
                             + (f": {unresolved}" if unresolved else "")})

    valid = all(c["passed"] for c in checks) and not errors
    return {"valid": valid, "errors": errors, "checks": checks,
            "embedded_count": len(embedded)}
