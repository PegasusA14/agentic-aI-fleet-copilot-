from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import ValidationError

from fleet_copilot.data.models import TelemetryRow


@dataclass(frozen=True)
class ParsedRow:
    line_no: int
    row: TelemetryRow


@dataclass(frozen=True)
class Quarantined:
    line_no: int
    reason: str


@dataclass(frozen=True)
class ParseResult:
    rows: list[ParsedRow]
    quarantined: list[Quarantined]


def _reason(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first["loc"]) or "<row>"
    return f"{location}: {first['msg']}"


def parse_ndjson(lines: Iterable[str]) -> ParseResult:
    """Validate each non-blank line. Bad lines are quarantined with a reason, never dropped silently."""
    rows: list[ParsedRow] = []
    quarantined: list[Quarantined] = []
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            rows.append(ParsedRow(line_no, TelemetryRow.model_validate_json(line)))
        except ValidationError as exc:
            quarantined.append(Quarantined(line_no, _reason(exc)))
    return ParseResult(rows, quarantined)
