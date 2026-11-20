"""Who is asking. Built by the server from a verified session, never from anything the client or model sends."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Literal

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Identity:
    """A signed-in person. `company_id` is the tenant every query is scoped to."""

    company_id: str
    actor_id: str
    display_name: str
    role: Literal["admin", "operator"] = "admin"

    def __post_init__(self) -> None:
        for value in (self.company_id, self.actor_id):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError("identity ids may only contain letters, digits, '_' and '-'")


# Demo sign-in: one fictional administrator per tenant in the dataset. There is no password to leak.
DEMO_IDENTITIES: dict[str, Identity] = {
    "admin-acme": Identity("acme-001", "admin-acme", "Acme admin (demo)"),
    "admin-globex": Identity("globex-002", "admin-globex", "Globex admin (demo)"),
    "admin-initech": Identity("initech-003", "admin-initech", "Initech admin (demo)"),
}

# The operator console login. It has no tenant: `operator` matches no company in the dataset, and the API
# refuses it on every chat and evidence route.
OPERATOR_IDENTITY = Identity("operator", "operator", "Operator (demo)", role="operator")


def new_conversation_id() -> str:
    return secrets.token_urlsafe(9)


def valid_conversation_id(conversation_id: str) -> bool:
    return bool(_SAFE_ID.fullmatch(conversation_id))


def thread_id(identity: Identity, conversation_id: str) -> str:
    """The checkpoint thread for one conversation. Company and actor are part of the key, so another
    tenant (or another person) using the same conversation id reads and writes a different thread.
    `:` is not allowed inside any part, so two different triples can never produce the same key."""
    if not valid_conversation_id(conversation_id):
        raise ValueError("invalid conversation id")
    return f"{identity.company_id}:{identity.actor_id}:{conversation_id}"
