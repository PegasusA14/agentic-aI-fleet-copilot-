from __future__ import annotations

from contextlib import contextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from fleet_copilot import policy
from fleet_copilot.agent.identity import Identity
from fleet_copilot.api.security import require_admin, require_identity, require_operator
from fleet_copilot.config import EXPECTED_DATASET_SHA256
from fleet_copilot.data.loader import connect
from fleet_copilot.evidence import EvidenceRef
from fleet_copilot.repository import FleetRepository, NotFound, TenantContext
from fleet_copilot.timeutil import iso_z

router = APIRouter(prefix="/api")
_MAX_REF_CHARS = 300


def _not_found() -> HTTPException:
    return HTTPException(404, "not found")


@contextmanager
def _tenant_repo(request: Request, identity: Identity):
    conn = connect(request.app.state.settings.db_path, read_only=True)
    try:
        yield FleetRepository(conn, TenantContext(identity.company_id, identity.actor_id))
    finally:
        conn.close()


@router.get("/evidence")
def get_evidence(
    request: Request, identity: Annotated[Identity, Depends(require_admin)], ref: str = ""
) -> dict:
    try:
        if not ref or len(ref) > _MAX_REF_CHARS:
            raise ValueError("malformed evidence reference")
        parsed = EvidenceRef.parse(ref)
        with _tenant_repo(request, identity) as repo:
            value = repo.resolve(parsed)
    except (ValueError, NotFound):
        # malformed, foreign and nonexistent refs all look the same
        raise _not_found() from None
    return {"ref": value.ref, "collected_at": value.collected_at, "value": value.value}


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, request: Request) -> dict:
    identity = require_admin(request)
    events = request.app.state.store.get_trace(identity.company_id, trace_id)
    if events is None:
        raise _not_found()
    return {"trace_id": trace_id, "events": [e.model_dump(mode="json") for e in events]}


@router.get("/policy")
def get_policy(request: Request) -> dict:
    require_identity(request)
    as_of = request.app.state.service.check_ready()
    return {
        "as_of": iso_z(as_of),
        "dataset_sha256": EXPECTED_DATASET_SHA256,
        "defaults": policy.disclosure(),
    }


@router.get("/observe/turns", dependencies=[Depends(require_operator)])
def observe_turns(request: Request, limit: Annotated[int, Query(ge=0, le=500)] = 50) -> list[dict]:
    return request.app.state.store.list_turns(limit=limit)


@router.get("/observe/traces/{trace_id}")
def observe_trace(trace_id: str, request: Request) -> dict:
    require_operator(request)
    events = request.app.state.store.get_trace_any(trace_id)
    if events is None:
        raise _not_found()
    return {"trace_id": trace_id, "events": [e.model_dump(mode="json") for e in events]}


@router.get("/observe/evals", dependencies=[Depends(require_operator)])
def observe_evals(request: Request, limit: Annotated[int, Query(ge=0, le=500)] = 20) -> list[dict]:
    """Stored evaluation runs, newest first, with their totals per suite and no cases."""
    return request.app.state.evals.list_runs(limit=limit)


@router.get("/observe/evals/{run_id}", dependencies=[Depends(require_operator)])
def observe_eval(run_id: str, request: Request) -> dict:
    run = request.app.state.evals.get_run(run_id)
    if run is None:
        raise _not_found()
    return run
