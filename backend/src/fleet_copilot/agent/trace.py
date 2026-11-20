"""Trace events: what the UI streams and the observability console stores.

Nodes emit small drafts through LangGraph's custom stream; the service stamps them with the turn's
`trace_id` and a sequence number. Drafts carry counts, tool names and redacted arguments only.
"""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

from fleet_copilot.contracts import TraceEvent, TraceKind

# Arguments whose values are enumerated or numeric, so they are safe to show. Everything else
# (hostnames, model names, employee ids, device ids) is reduced to "applied" or a count.
SAFE_ARG_KEYS = frozenset(
    {
        "metric",
        "window_days",
        "threshold_pct",
        "older_than",
        "check_id",
        "severity",
        "form_factor",
        "os_name",
        "group_by",
    }
)


def trace_args(args: dict[str, Any]) -> dict[str, Any]:
    """Arguments as they may appear in a trace. Unset arguments are left out."""
    shown: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        if key == "device_ids":
            shown[key] = len(value)
        elif key in SAFE_ARG_KEYS and isinstance(value, str | int | float | bool):
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
