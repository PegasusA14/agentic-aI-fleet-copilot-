from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fleet_copilot.fleet.queries import QueryResult

_MAX_IDS_IN_VIEW = 30


def redact_args(args: dict[str, Any]) -> dict[str, Any]:
    """Copy of `args` that is safe to record: the employee id narrows the query but is never echoed."""
    if args.get("employee_id"):
        return {**args, "employee_id": "applied"}
    return dict(args)


def redact_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of `AIMessage.tool_calls` entries with redacted args, for traces and persisted state."""
    return [{**call, "args": redact_args(call.get("args") or {})} for call in tool_calls]


class FindingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    hostname: str
    values: dict[str, Any]
    refs: list[str]


class ExcludedInfo(BaseModel):
    """Why one device was left out of a check, in words. Presentation only: the model never reads it."""

    model_config = ConfigDict(extra="forbid")

    hostname: str
    text: str


class ToolRecord(BaseModel):
    """A typed, JSON-safe copy of one tool call's result. The composer reads this, not prose."""

    model_config = ConfigDict(extra="forbid")

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    error: Literal["not_found", "tool_error"] | None = None
    findings: list[FindingRecord] = Field(default_factory=list)
    population: int = 0
    as_of: datetime | None = None
    rule: str = ""
    excluded: dict[str, list[str]] = Field(default_factory=dict)
    # reason -> device id -> hostname and reason text, for the devices in `excluded`.
    excluded_detail: dict[str, dict[str, ExcludedInfo]] = Field(default_factory=dict)
    stale: list[str] = Field(default_factory=list)
    missing_latest: list[str] = Field(default_factory=list)
    # Requested `device_ids` that are not this tenant's devices. Foreign and nonexistent ids count alike.
    unresolved_ids: int = Field(default=0, ge=0)
    # The proposal tools only: the code-written card of the proposal that was made, or why none was.
    proposal: dict[str, Any] | None = None
    action_refusal: dict[str, str] | None = None
    # `recommend_actions` only: the code-written suggestions (empty when nothing is eligible). `findings`
    # then lists the devices that were considered, so the composer has their hostnames.
    suggestions: list[dict[str, Any]] | None = None
    # The requested action type that no device qualified for, when advice fell back to every eligible action.
    advice_fallback: str | None = None

    @classmethod
    def from_query(cls, tool: str, args: dict[str, Any], result: QueryResult) -> ToolRecord:
        return cls(
            tool=tool,
            args=args,
            findings=[
                FindingRecord(
                    device_id=f.device_id,
                    hostname=f.hostname,
                    values=dict(f.values),
                    refs=list(f.refs),
                )
                for f in result.findings
            ],
            population=result.population,
            as_of=result.as_of,
            rule=result.rule,
            excluded={reason: list(ids) for reason, ids in result.excluded.items()},
            excluded_detail={
                reason: {
                    device_id: ExcludedInfo(hostname=hostname, text=text)
                    for device_id, (hostname, text) in found.items()
                }
                for reason, found in result.excluded_detail.items()
            },
            stale=list(result.stale),
            missing_latest=list(result.missing_latest),
        )

    @property
    def device_ids(self) -> list[str]:
        """Each device once, in finding order (a device can have several findings, one per app)."""
        return list(dict.fromkeys(f.device_id for f in self.findings))

    def llm_view(self) -> str:
        """What the model reads: counts and ids only. It never sees raw values to recompute."""
        if self.error == "not_found":
            return json.dumps(
                {
                    "tool": self.tool,
                    "error": "not_found",
                    "note": "No matching device in this company.",
                }
            )
        if self.error:
            return json.dumps(
                {"tool": self.tool, "error": self.error, "note": "The data lookup failed."}
            )
        if self.proposal is not None or self.action_refusal is not None:
            # Nothing but the outcome: no card text, no employee, no evidence refs reach the model.
            view = {
                "tool": self.tool,
                "status": "proposed" if self.proposal is not None else "refused",
                "action_type": (self.proposal or {}).get("action_type")
                or self.args.get("action_type"),
            }
            if self.action_refusal is not None:
                view["reason"] = self.action_refusal["reason"]
            return json.dumps(view, sort_keys=True)
        if self.suggestions is not None:
            # Counts and action types only: no reasons, evidence refs, hostnames or employees.
            return json.dumps(
                {
                    "tool": self.tool,
                    "status": "recommended",
                    "device_count": self.population,
                    "suggestion_count": len(self.suggestions),
                    "action_types": dict(Counter(s["action_type"] for s in self.suggestions)),
                },
                sort_keys=True,
            )
        view: dict[str, Any] = {
            "tool": self.tool,
            "rule": self.rule,
            "population": self.population,
            "match_count": len(self.findings),
            "device_ids": self.device_ids[:_MAX_IDS_IN_VIEW],
            "excluded_counts": {reason: len(ids) for reason, ids in self.excluded.items()},
            "stale_count": len(self.stale),
            "missing_latest_count": len(self.missing_latest),
        }
        classes = Counter(
            f.values.get("trend") or f.values.get("kind")
            for f in self.findings
            if f.values.get("trend") or f.values.get("kind")
        )
        if classes:
            view["by_class"] = dict(classes)
        return json.dumps(view, sort_keys=True)


def record_from_message(message: ToolMessage, *, tool: str, args: dict[str, Any]) -> ToolRecord:
    """The record a tool call produced, or a `tool_error` record when it failed.

    A failed call has an error message and no artifact, so the failure is rebuilt from what the graph
    knows (tool and arguments). The message content is never copied into the record.
    """
    if message.status == "success":
        try:
            return ToolRecord.model_validate(message.artifact)
        except ValidationError:
            pass
    return ToolRecord(tool=tool, args=redact_args(args), error="tool_error")
