"""Trace events: what the UI streams and the observability console stores.

Nodes emit small drafts through LangGraph's custom stream; the service stamps them with the turn's
`trace_id` and a sequence number. Drafts carry counts, tool names and redacted arguments only.
"""

from __future__ import annotations

import re
from typing import Any, get_args

from langgraph.config import get_stream_writer

from fleet_copilot.agent.tools import (
    CheckId,
    Component,
    FormFactor,
    OsName,
    Severity,
    SoftwareView,
    Topic,
)
from fleet_copilot.contracts import ActionType, TraceEvent, TraceKind

# Arguments whose values are enumerated or numeric. Model-supplied text is not trusted just because the
# key is a safe one: a string is shown only if it is one of the values the tool accepts for that key
# (or, for versions, has the version shape); anything else is reduced to "applied". Everything else
# (hostnames, model names, employee ids, device ids) is "applied" or a count.
_ENUMERATED: dict[str, frozenset[str]] = {
    "metric": frozenset(
        {"memory", "disk", "compliance", "battery"}
        | {"disk_free_pct", "memory_used_pct", "battery_capacity"}
    ),
    "check_id": frozenset(get_args(CheckId)),
    "severity": frozenset(get_args(Severity)),
    "form_factor": frozenset(get_args(FormFactor)),
    "os_name": frozenset(get_args(OsName)),
    "group_by": frozenset({"os", "model", "form_factor"}),
    "view": frozenset(get_args(SoftwareView)),
    "action_type": frozenset(get_args(ActionType)),
    "component": frozenset(get_args(Component)),
    "topic": frozenset(get_args(Topic)),
}
_VERSION = re.compile(r"^\d{1,3}(\.\d{1,3}){0,2}$")
_NUMERIC_KEYS = frozenset({"window_days", "threshold_pct"})
SAFE_ARG_KEYS = frozenset(_ENUMERATED) | _NUMERIC_KEYS | {"older_than"}


_MAX_SHOWN_NUMBER = 1_000_000


def _safe_value(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return False  # a bool is never a value any tool takes under these keys
    if isinstance(value, int | float):
        # Numbers are model-controlled: only a plain integer of count size is shown, never a float
        # (NaN, infinity, long fractions) or a huge integer that could carry text.
        return key in _NUMERIC_KEYS and isinstance(value, int) and 0 <= value <= _MAX_SHOWN_NUMBER
    if not isinstance(value, str):
        return False
    if key == "older_than":
        return bool(_VERSION.match(value))
    return value in _ENUMERATED.get(key, frozenset())


def trace_args(args: dict[str, Any]) -> dict[str, Any]:
    """Arguments as they may appear in a trace. Unset arguments are left out."""
    shown: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        if key == "device_ids":
            shown[key] = len(value) if isinstance(value, list | tuple) else "applied"
        elif _safe_value(key, value):
            shown[key] = value
        else:
            shown[key] = "applied"
    return shown


def emit(
    kind: TraceKind,
    summary: str,
    *,
    tool: str | None = None,
    data: dict[str, Any] | None = None,
    latency_ms: int | None = None,
) -> None:
    """Send one trace draft to whoever is streaming this turn (a no-op when nobody is)."""
    try:
        writer = get_stream_writer()
    except RuntimeError:  # called outside a graph run, for example in a unit test
        return
    writer(
        {
            "type": "trace",
            "kind": kind,
            "summary": summary,
            "tool": tool,
            "data": data or {},
            "latency_ms": latency_ms,
        }
    )


def to_event(draft: dict[str, Any], *, trace_id: str, seq: int) -> TraceEvent:
    return TraceEvent(
        trace_id=trace_id,
        seq=seq,
        kind=draft["kind"],
        tool=draft.get("tool"),
        summary=draft["summary"],
        data=draft.get("data") or {},
        latency_ms=draft.get("latency_ms"),
    )
