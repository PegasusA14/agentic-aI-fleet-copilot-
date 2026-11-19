from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# backend/src/fleet_copilot/config.py -> repository root is three levels above the package dir.
REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_DATASET_SHA256 = "fb0ee31ffeeb731b919fc4d5124098c9c5b68cf3fba35eed5092a9f73655c4de"
PINNED_AS_OF = "2026-06-12T11:00:00Z"


@dataclass(frozen=True)
class Settings:
    data_path: Path
    db_path: Path

    @classmethod
    def from_env(cls) -> Settings:
        data = (
            os.environ.get("FLEET_DATA_PATH")
            or REPO_ROOT / "data" / "device-telemetry-dataset.ndjson"
        )
        db = os.environ.get("FLEET_DB_PATH") or REPO_ROOT / "var" / "fleet.db"
        return cls(data_path=Path(data), db_path=Path(db))
