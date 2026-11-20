"""`python -m fleet_copilot.api`: the local server. The host is not configurable on purpose."""

from __future__ import annotations

import os

import uvicorn

from fleet_copilot.api.app import create_app_from_env
from fleet_copilot.config import load_env

HOST = "127.0.0.1"


def main() -> None:
    load_env()
    uvicorn.run(
        create_app_from_env(),
        host=HOST,
        port=int(os.environ.get("FLEET_PORT") or 8000),
        log_level="warning",
        access_log=False,  # request lines would carry evidence refs
    )


if __name__ == "__main__":
    main()
