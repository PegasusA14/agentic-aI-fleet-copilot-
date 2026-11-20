from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from fleet_copilot.agent.identity import DEMO_IDENTITIES
from fleet_copilot.api.security import ALL_USERS, require_identity

router = APIRouter(prefix="/api")


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(max_length=40)
    passcode: str | None = Field(default=None, max_length=200)


def _view(identity) -> dict[str, str]:
    return {
        "company_id": identity.company_id,
        "display_name": identity.display_name,
        "role": identity.role,
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/demo-users")
def demo_users() -> list[dict[str, str]]:
    return [
        {"user": key, "display_name": i.display_name, "company_id": i.company_id}
        for key, i in DEMO_IDENTITIES.items()
    ]


@router.post("/session")
async def sign_in(body: SignIn, request: Request) -> dict[str, str]:
    api = request.app.state.api_settings
    identity = ALL_USERS.get(body.user)
    if identity is not None and identity.role == "operator":
        expected = api.operator_passcode
        if expected is None:
            raise HTTPException(403, "operator sign-in is not enabled")
        given = (body.passcode or "").encode()
        if not hmac.compare_digest(given, expected.get_secret_value().encode()):
            identity = None
    if identity is None:
        await asyncio.sleep(api.failure_delay_s)
        raise HTTPException(401, "sign-in failed")
    request.session.clear()
    request.session["user"] = body.user
    return _view(identity)


@router.get("/session")
def current_session(request: Request) -> dict[str, str]:
    return _view(require_identity(request))


@router.delete("/session", status_code=204)
def sign_out(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)
