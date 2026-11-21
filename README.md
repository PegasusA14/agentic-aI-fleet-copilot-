# Fleet Copilot

A natural-language copilot that turns device telemetry into evidence-backed fleet
decisions and human-approved actions. Built for the Senior Agentic AI Engineer
assessment.

> Status: the agent, API, web console and evaluation suites run end to end. `make eval` replays the committed
> cassette; until `evals/cassettes/eval.jsonl` is recorded with `make eval-live`, every case reports "not recorded".

## Quick start

```bash
make setup        # install backend dependencies
make load-data    # load the dataset into var/fleet.db
make frontend-install   # install frontend dependencies (once)
make demo         # build the frontend, then serve app and API at http://127.0.0.1:8000
```

`make demo` needs a `.env` with the model settings (copy `.env.example`); it builds the
frontend into `frontend/dist`, and the API serves it. The API only serves the build when
`frontend/dist/index.html` exists, so `make api` alone still works without it. Set
`FLEET_STATIC_DIR` to serve a build from another directory.

For development, run `make dev`. It starts the API on `127.0.0.1:8000` and the Vite dev
server on `127.0.0.1:5173` (open this one), which proxies `/api` to the API. Stop both
with Ctrl+C.

To try the interface with no backend and no model, use fixture mode:

```bash
cd frontend && VITE_FLEET_DATA=fixtures npm run dev
```

Tests: `make test` (backend), `make frontend-test` (typecheck and unit tests), `make lint`.
Evaluation: `make eval` (offline replay) and `make eval-live` (Azure, records the cassette); see
[Evaluation](backend/README.md#evaluation).

## Setup

Requirements: [`uv`](https://docs.astral.sh/uv/) (it provisions Python 3.14).

```bash
make setup       # install backend dependencies
make load-data   # validate data/device-telemetry-dataset.ndjson into var/fleet.db
make test        # unit, golden-agreement and tenant-isolation tests (no API key needed)
make lint        # ruff checks on backend/src and backend/tests
make golden      # regenerate evals/golden/golden.json from the raw data
make eval        # run the four evaluation suites offline from the committed cassette
make contracts   # regenerate the contracts/*.schema.json files from the Pydantic models
```

`make golden` runs `evals/golden/oracle.py` with the system `python3` (standard
library only, no dependencies).

"Now" is pinned to the newest snapshot in the dataset (2026-06-12T11:00:00Z), not the
wall clock.

## API contract

`backend/src/fleet_copilot/contracts.py` defines the typed answer envelope and trace
events. `contracts/AnswerEnvelope.schema.json` and `contracts/TraceEvent.schema.json` are
the JSON Schemas generated from those models with `make contracts`, in serialization
mode so every field is required in the generated types. They are committed, and a test
fails if they drift from the models, so rerun `make contracts` after changing them.
