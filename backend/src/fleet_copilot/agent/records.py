from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fleet_copilot.fleet.queries import QueryResult

_MAX_IDS_IN_VIEW = 30


class FindingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    hostname: str
    values: dict[str, Any]
    refs: list[str]


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
    stale: list[str] = Field(default_factory=list)
    missing_latest: list[str] = Field(default_factory=list)
    # Requested `device_ids` that are not this tenant's devices. Foreign and nonexistent ids count alike.
    unresolved_ids: int = Field(default=0, ge=0)

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
            stale=list(result.stale),
            missing_latest=list(result.missing_latest),
        )

    @property
    def device_ids(self) -> list[str]:
        return [f.device_id for f in self.findings]

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
