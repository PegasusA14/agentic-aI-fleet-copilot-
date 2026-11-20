"""Who is calling. The session cookie holds a demo user key; this module maps it to an `Identity`."""

from __future__ import annotations

from fastapi import HTTPException, Request

from fleet_copilot.agent.identity import DEMO_IDENTITIES, OPERATOR_IDENTITY, Identity

ALL_USERS: dict[str, Identity] = {**DEMO_IDENTITIES, "operator": OPERATOR_IDENTITY}


def identity_of(request: Request) -> Identity | None:
    return ALL_USERS.get(str(request.session.get("user", "")))


def require_identity(request: Request) -> Identity:
    identity = identity_of(request)
    if identity is None:
        raise HTTPException(401, "sign in first")
    return identity


def require_admin(request: Request) -> Identity:
    identity = require_identity(request)
    if identity.role != "admin":
        raise HTTPException(403, "not allowed")
    return identity


def require_operator(request: Request) -> Identity:
    identity = require_identity(request)
    if identity.role != "operator":
        raise HTTPException(403, "not allowed")
    return identity
