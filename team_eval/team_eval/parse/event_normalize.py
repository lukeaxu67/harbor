"""Normalize raw Claude Code/FleetView jsonl records into StepRecord turns.

Mirrors the approach in harbor's ``claude_code.py`` (uuid dedup, timestamp sort,
one ATIF step per assistant inference keyed by ``message.id``, usage→metrics)
but is a standalone, team-focused implementation. It does not try to reproduce
every production edge case — the goal is a faithful-enough turn sequence for
graphing, ATIF conversion, evaluation and visualization.
"""

from __future__ import annotations

import json
from typing import Any

from team_eval.parse.models import (
    Source,
    StepRecord,
    ToolResultRecord,
    ToolUseRecord,
    _stringify,
    _truncate,
)

_VALID_TYPES = {"user", "assistant", "system"}


def _parse_tool_use(block: dict[str, Any]) -> ToolUseRecord:
    tool_use_id = block.get("id")
    name = block.get("name") or ""
    inp = block.get("input")
    raw = None
    if isinstance(inp, str):
        raw = inp
        try:
            inp = json.loads(inp) if inp.strip() else {}
        except json.JSONDecodeError:
            inp = {"_raw": inp}
    elif not isinstance(inp, dict):
        inp = {} if inp is None else {"_value": inp}
    return ToolUseRecord(tool_use_id=tool_use_id, name=name, arguments=inp, raw=raw)


def extract_assistant(content: Any) -> tuple[str, str | None, list[ToolUseRecord]]:
    """Return (text, reasoning, tool_uses) from an assistant message content."""
    if isinstance(content, str):
        return content.strip(), None, []
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_uses: list[ToolUseRecord] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                text_parts.append(_stringify(block))
                continue
            btype = block.get("type")
            if btype == "tool_use":
                tool_uses.append(_parse_tool_use(block))
                continue
            if btype in {"thinking", "reasoning", "analysis"}:
                tv = block.get("text")
                if isinstance(tv, str):
                    reasoning_parts.append(tv.strip())
                continue
            if btype == "code" and isinstance(block.get("code"), str):
                text_parts.append(block["code"])
                continue
            tv = block.get("text")
            if isinstance(tv, str):
                text_parts.append(tv)
            elif tv is None and btype not in {None, "text"}:
                text_parts.append(_stringify(block))
    elif content is not None:
        text_parts.append(_stringify(content))
    text = "\n\n".join(p for p in text_parts if p and p.strip())
    reasoning = "\n\n".join(p for p in reasoning_parts if p and p.strip()) or None
    return text, reasoning, tool_uses


def parse_tool_results(content: Any) -> list[ToolResultRecord]:
    """Extract tool_result blocks from a user message content."""
    out: list[ToolResultRecord] = []
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        raw = block.get("content")
        if isinstance(raw, list):
            parts = []
            for b in raw:
                if isinstance(b, dict) and isinstance(b.get("text"), str):
                    parts.append(b["text"])
                elif isinstance(b, str):
                    parts.append(b)
            text = "\n".join(parts)
        elif isinstance(raw, str):
            text = raw
        else:
            text = _stringify(raw)
        out.append(
            ToolResultRecord(
                tool_use_id=block.get("tool_use_id"),
                content=_truncate(text, 4000),
                is_error=bool(block.get("is_error")),
            )
        )
    return out


def extract_user_text(content: Any) -> str:
    """Text of a user message, ignoring tool_result blocks (e.g. teammate-message)."""
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return _stringify(content).strip()
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "tool_result":
                continue
            tv = block.get("text")
            if isinstance(tv, str):
                parts.append(tv)
            elif block.get("type") not in {None, "text"}:
                parts.append(_stringify(block))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p and p.strip())


def normalize_events(records: list[dict[str, Any]]) -> list[StepRecord]:
    """Convert raw jsonl records into sequential StepRecord turns (step_id from 1)."""
    # Keep only dialogue-bearing records.
    kept = [r for r in records if r.get("type") in _VALID_TYPES]

    # Dedup by uuid (keep first).
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in kept:
        uid = r.get("uuid")
        if isinstance(uid, str) and uid:
            if uid in seen:
                continue
            seen.add(uid)
        deduped.append(r)

    deduped.sort(key=lambda r: r.get("timestamp") or "")

    steps: list[StepRecord] = []
    pending: StepRecord | None = None  # current agent turn being assembled

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            steps.append(pending)
            pending = None

    def attach_results(results: list[ToolResultRecord]) -> None:
        nonlocal pending
        target = pending
        if target is None and steps and steps[-1].source == "agent":
            target = steps[-1]
        if target is not None:
            target.tool_results.extend(results)
        else:
            # orphan observations → system step
            flush()
            steps.append(
                StepRecord(
                    step_id=0,
                    source="system",
                    text="",
                    tool_results=results,
                )
            )

    for rec in deduped:
        rtype = rec.get("type")
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        ts = rec.get("timestamp")
        uuid = rec.get("uuid")
        sidechain = bool(rec.get("isSidechain"))

        if rtype == "assistant":
            text, reasoning, tool_uses = extract_assistant(content)
            usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
            model = msg.get("model")
            msg_id = msg.get("id")
            if (
                pending is not None
                and msg_id
                and pending.extra
                and pending.extra.get("_msgid") == msg_id
            ):
                # Merge a streamed chunk of the same inference.
                if len(text) > len(pending.text):
                    pending.text = text
                if reasoning and len(reasoning) > len(pending.reasoning or ""):
                    pending.reasoning = reasoning
                existing = {tu.tool_use_id for tu in pending.tool_uses}
                for tu in tool_uses:
                    if tu.tool_use_id not in existing:
                        pending.tool_uses.append(tu)
                        existing.add(tu.tool_use_id)
                if usage:
                    pending.usage = usage
                if model:
                    pending.model_name = model
                pending.timestamp = ts or pending.timestamp
            else:
                flush()
                pending = StepRecord(
                    step_id=0,
                    timestamp=ts,
                    source="agent",
                    text=text,
                    reasoning=reasoning,
                    model_name=model,
                    usage=usage,
                    raw_uuid=uuid,
                    is_sidechain=sidechain,
                    extra={"_msgid": msg_id},
                )
                pending.tool_uses = tool_uses

        elif rtype == "user":
            results = parse_tool_results(content)
            user_text = extract_user_text(content)
            if results:
                attach_results(results)
            if user_text:
                flush()
                steps.append(
                    StepRecord(
                        step_id=0,
                        timestamp=ts,
                        source="user",
                        text=user_text,
                        raw_uuid=uuid,
                        is_sidechain=sidechain,
                    )
                )

        elif rtype == "system":
            text = (
                content.strip()
                if isinstance(content, str)
                else extract_user_text(content)
            )
            flush()
            steps.append(
                StepRecord(
                    step_id=0,
                    timestamp=ts,
                    source="system",
                    text=text,
                    raw_uuid=uuid,
                    is_sidechain=sidechain,
                )
            )

    flush()

    # Drop the helper _msgid before finalizing; assign sequential ids.
    for i, step in enumerate(steps, start=1):
        step.step_id = i
        if step.extra and "_msgid" in step.extra:
            mid = step.extra.pop("_msgid")
            if step.extra:
                step.extra["message_id"] = mid
            else:
                step.extra = {"message_id": mid} if mid else None
    return steps
