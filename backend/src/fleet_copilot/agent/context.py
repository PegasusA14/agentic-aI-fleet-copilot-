from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class AgentContext:
    """Server-side, per-request context. Never supplied or altered by the model."""

    db_path: Path
    company_id: str
    actor_id: str
    as_of: datetime
    trace_id: str = ""
    company_name: str = ""  # display name for the planner prompt only; never a tool argument
    actions_db_path: Path | None = (
        None  # the actions store; None means the proposal tools cannot run
    )
