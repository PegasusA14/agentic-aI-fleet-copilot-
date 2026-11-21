"""Identifiers per company, read straight from the raw dataset file with the standard library.

The evaluators and `live_check` use these to prove that nothing of another company leaks into an answer,
so they must not depend on the product code that is under test.
"""

from __future__ import annotations

import json
from functools import lru_cache

from fleet_copilot.config import REPO_ROOT

DATASET = REPO_ROOT / "data" / "device-telemetry-dataset.ndjson"
COMPANY_IDS = ("acme-001", "globex-002", "initech-003")


@lru_cache(maxsize=1)
def identifiers_by_tenant() -> dict[str, frozenset[str]]:
    """Device ids and hostnames per company, read straight from the dataset file (no product code)."""
    found: dict[str, set[str]] = {}
    with DATASET.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            names = found.setdefault(row["company_id"], set())
            names.add(row["device_id"])
            names.add(row["os"]["hostname"])
    return {company: frozenset(names) for company, names in found.items()}


def foreign_identifiers(tenant: str) -> frozenset[str]:
    """Every device id and hostname of the other companies (minus any the tenant itself also uses)."""
    by_tenant = identifiers_by_tenant()
    own = by_tenant.get(tenant, frozenset())
    return (
        frozenset(n for company, names in by_tenant.items() if company != tenant for n in names)
        - own
    )
