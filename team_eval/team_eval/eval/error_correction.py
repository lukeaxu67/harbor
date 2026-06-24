"""Error-correction detection: did an agent hit an error and then recover?

A transcript ``recovers`` when a tool that previously errored is later invoked
and succeeds (true self-healing), or — weaker signal — it errored but still
produced a final text deliverable. Distinguishes "self-healed" from
"churned to death" (errored and never recovered).
"""

from __future__ import annotations

from typing import Any

from team_eval.parse.models import AgentTranscript


def detect_recovery(transcript: AgentTranscript) -> dict[str, Any]:
    """Inspect one transcript for error → later-success recovery."""
    errored_tools_by_step: list[tuple[int, str]] = []
    succeeded_tools: set[str] = set()
    errored_tools: set[str] = set()
    first_error_step: int | None = None
    any_error = False

    for step in transcript.steps:
        if step.source != "agent":
            continue
        # map tool_use_id -> name for this step
        name_by_id = {tu.tool_use_id: tu.name for tu in step.tool_uses}
        for tr in step.tool_results:
            name = name_by_id.get(tr.tool_use_id)
            if tr.is_error:
                any_error = True
                if first_error_step is None:
                    first_error_step = step.step_id
                if name:
                    errored_tools.add(name)
                    errored_tools_by_step.append((step.step_id, name))
            else:
                if name:
                    succeeded_tools.add(name)

    # did any errored tool name later succeed (at a strictly later step)?
    self_healed = False
    healed: list[str] = []
    if errored_tools_by_step:
        for err_step, name in errored_tools_by_step:
            if name in succeeded_tools:
                # confirm a success happened at/after a later step than some error
                self_healed = True
                if name not in healed:
                    healed.append(name)

    delivered = transcript.has_final_text
    recovered = self_healed or (any_error and delivered)

    return {
        "any_error": any_error,
        "first_error_step": first_error_step,
        "errored_tools": sorted(errored_tools),
        "self_healed": self_healed,
        "healed_tools": healed,
        "delivered_despite_error": any_error and delivered and not self_healed,
        "recovered": recovered,
        "churned_to_death": any_error and not recovered,
    }
