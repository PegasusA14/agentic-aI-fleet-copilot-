"""`python -m fleet_copilot.api`: the local server.

The host is not configurable. On a machine the server listens on 127.0.0.1 only. Inside the container image
(which sets FLEET_CONTAINER=1) it listens on all of the container's interfaces, because a published port can only
reach a process that does; docker-compose.yml then publishes that port on the host's 127.0.0.1 only.
"""

from __future__ import annotations

import os

import uvicorn

from fleet_copilot.api.app import create_app_from_env
from fleet_copilot.config import load_env

HOST = "127.0.0.1"
CONTAINER_HOST = "0.0.0.0"  # noqa: S104 - container-internal; the host publishes it on 127.0.0.1 only


def bind_host(env: dict[str, str] | None = None) -> str:
    """127.0.0.1, or every interface inside the container image (FLEET_CONTAINER=1 and nothing else)."""
    env = dict(os.environ) if env is None else env
    return CONTAINER_HOST if env.get("FLEET_CONTAINER") == "1" else HOST


def main() -> None:
    load_env()
    uvicorn.run(
        create_app_from_env(),
        host=bind_host(),
        port=int(os.environ.get("FLEET_PORT") or 8000),
        log_level="warning",
        access_log=False,  # request lines would carry evidence refs
    )


if __name__ == "__main__":
    main()
