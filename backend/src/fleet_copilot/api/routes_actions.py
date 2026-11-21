"""Approval, rejection and audit of action proposals. Only an admin of the owning tenant decides."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from fleet_copilot.actions.service import ActionService, ProposalConflict, ProposalNotFound
from fleet_copilot.agent.identity import Identity
from fleet_copilot.api.security import require_admin, require_operator
from fleet_copilot.contracts import ProposalStatus

router = APIRouter(prefix="/api")
_MAX_ID_CHARS = 64


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload_hash: str = Field(max_length=128)


def _actions(request: Request) -> ActionService:
    return request.app.state.actions


def _not_found() -> HTTPException:
    return HTTPException(404, "not found")


def _conflict(exc: ProposalConflict) -> HTTPException:
    if exc.reason.startswith("not_pending:"):
        return HTTPException(409, f"proposal is {exc.reason.split(':', 1)[1]}")
    return HTTPException(409, "proposal changed")


@router.get("/proposals")
def list_proposals(
    request: Request,
    identity: Annotated[Identity, Depends(require_admin)],
    status: ProposalStatus | None = None,
) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") for c in _actions(request).list(identity, status)]


@router.get("/proposals/{proposal_id}")
def get_proposal(
    proposal_id: str, request: Request, identity: Annotated[Identity, Depends(require_admin)]
) -> dict[str, Any]:
    try:
        return _actions(request).get(identity, proposal_id).model_dump(mode="json")
    except ProposalNotFound:
        raise _not_found() from None


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(
    proposal_id: str,
    body: ApproveRequest,
    request: Request,
    identity: Annotated[Identity, Depends(require_admin)],
) -> dict[str, Any]:
    try:
        decision = _actions(request).approve(identity, proposal_id, body.payload_hash)
    except ProposalNotFound:
        raise _not_found() from None
    except ProposalConflict as exc:
        raise _conflict(exc) from None
    except PermissionError:
        raise HTTPException(403, "not allowed") from None
    return {
        "proposal": decision.card.model_dump(mode="json"),
        "effect": decision.effect,
        "duplicate": decision.duplicate,
    }


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: str, request: Request, identity: Annotated[Identity, Depends(require_admin)]
) -> dict[str, Any]:
    try:
        decision = _actions(request).reject(identity, proposal_id)
    except ProposalNotFound:
        raise _not_found() from None
    except ProposalConflict as exc:
        raise _conflict(exc) from None
    except PermissionError:
        raise HTTPException(403, "not allowed") from None
    return {"proposal": decision.card.model_dump(mode="json")}


@router.get("/audit")
def audit(
    request: Request,
    identity: Annotated[Identity, Depends(require_admin)],
    proposal_id: Annotated[str | None, Query(max_length=_MAX_ID_CHARS)] = None,
    target: Annotated[str | None, Query(max_length=_MAX_ID_CHARS)] = None,
) -> list[dict[str, Any]]:
    return _actions(request).audit(identity, proposal_id=proposal_id, target_device_id=target)


@router.get("/observe/audit", dependencies=[Depends(require_operator)])
def observe_audit(
    request: Request,
    company: Annotated[str | None, Query(max_length=_MAX_ID_CHARS)] = None,
    actor: Annotated[str | None, Query(max_length=_MAX_ID_CHARS)] = None,
    target: Annotated[str | None, Query(max_length=_MAX_ID_CHARS)] = None,
) -> list[dict[str, Any]]:
    return _actions(request).audit_all(company_id=company, actor_id=actor, target_device_id=target)


@router.get("/observe/effects", dependencies=[Depends(require_operator)])
def observe_effects(request: Request) -> dict[str, dict[str, int]]:
    return _actions(request).effects()
