from __future__ import annotations

import json
from typing import Any

from fleet_copilot.config import REPO_ROOT
from fleet_copilot.contracts import AnswerEnvelope, TraceEvent

SCHEMA_PATH = REPO_ROOT / "contracts" / "schema.json"


def build_schema() -> dict[str, Any]:
    return {
        "AnswerEnvelope": AnswerEnvelope.model_json_schema(),
        "TraceEvent": TraceEvent.model_json_schema(),
    }


def main() -> None:
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(
        json.dumps(build_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
