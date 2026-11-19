# Fleet Copilot

A natural-language copilot that turns device telemetry into evidence-backed fleet
decisions and human-approved actions. Built for the Senior Agentic AI Engineer
assessment.

> Status: foundation layer. The agent, API and UI arrive in later plans.

## Setup

Requirements: [`uv`](https://docs.astral.sh/uv/) (it provisions Python 3.14).

```bash
make setup       # install backend dependencies
make load-data   # validate data/device-telemetry-dataset.ndjson into var/fleet.db
make test        # unit, golden-agreement and tenant-isolation tests (no API key needed)
make lint        # ruff checks on backend/src and backend/tests
make golden      # regenerate evals/golden/golden.json from the raw data
make contracts   # regenerate contracts/schema.json from the Pydantic models
```

`make golden` runs `evals/golden/oracle.py` with the system `python3` (standard
library only, no dependencies).

"Now" is pinned to the newest snapshot in the dataset (2026-06-12T11:00:00Z), not the
wall clock.

## API contract

`backend/src/fleet_copilot/contracts.py` defines the typed answer envelope and trace
events. `contracts/schema.json` is the JSON Schema generated from those models; it is
committed, and a test fails if it drifts from the models, so rerun `make contracts`
after changing them.
