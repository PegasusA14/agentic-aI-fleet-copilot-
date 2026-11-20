from __future__ import annotations

import json
from typing import Any

from fleet_copilot.config import REPO_ROOT
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent

SCHEMA_DIR = REPO_ROOT / "contracts"


def build_schema() -> dict[str, dict[str, Any]]:
    """One JSON Schema per top-level model, in serialization mode (what the API emits)."""
    return {
        "AnswerEnvelope": AnswerEnvelope.model_json_schema(mode="serialization"),
        "TraceEvent": TraceEvent.model_json_schema(mode="serialization"),
    }


def main() -> None:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for name, schema in build_schema().items():
        path = SCHEMA_DIR / f"{name}.schema.json"
        path.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    (SCHEMA_DIR / "schema.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
