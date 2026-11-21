from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

# backend/src/fleet_copilot/config.py -> repository root is three levels above the package dir.
REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_DATASET_SHA256 = "fb0ee31ffeeb731b919fc4d5124098c9c5b68cf3fba35eed5092a9f73655c4de"
PINNED_AS_OF = "2026-06-12T11:00:00Z"

ModelMode = Literal["replay", "live"]
_AZURE_VARS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
    "AZURE_OPENAI_DEPLOYMENT",
)


class ConfigError(ValueError):
    """Settings are missing or unusable. The message names variables, never their values."""


def load_env() -> None:
    """Load the git-ignored `.env` into the process environment. Entry points call this; tests never do."""
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    data_path: Path
    db_path: Path
    state_dir: Path | None = None
    static_dir: Path | None = (
        None  # the built frontend; the API serves it only if it has index.html
    )

    @classmethod
    def from_env(cls) -> Settings:
        data = (
            os.environ.get("FLEET_DATA_PATH")
            or REPO_ROOT / "data" / "device-telemetry-dataset.ndjson"
        )
        db = os.environ.get("FLEET_DB_PATH") or REPO_ROOT / "var" / "fleet.db"
        state = os.environ.get("FLEET_STATE_DIR") or None
        static = os.environ.get("FLEET_STATIC_DIR") or REPO_ROOT / "frontend" / "dist"
        return cls(
            data_path=Path(data),
            db_path=Path(db),
            state_dir=Path(state) if state else None,
            static_dir=Path(static),
        )

    def state_path(self) -> Path:
        """Where traces and conversation checkpoints live."""
        return self.state_dir or self.db_path.parent


@dataclass(frozen=True)
class AzureSettings:
    api_key: SecretStr
    endpoint: str = field(repr=False)
    api_version: str
    deployment: str = field(repr=False)


@dataclass(frozen=True)
class ModelSettings:
    mode: ModelMode
    azure: AzureSettings | None
    cassette_path: Path
    record: bool
    call_timeout_s: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ModelSettings:
        env = os.environ if env is None else env
        raw_mode = (env.get("FLEET_MODEL_MODE") or "replay").strip().lower()
        if raw_mode not in ("replay", "live"):
            raise ConfigError("FLEET_MODEL_MODE must be 'replay' or 'live'")
        from fleet_copilot import (
            policy,
        )  # local import: policy has no dependencies, keep config light

        azure: AzureSettings | None = None
        if raw_mode == "live":
            values = {name: (env.get(name) or "").strip() for name in _AZURE_VARS}
            missing = [name for name, value in values.items() if not value]
            if missing:
                raise ConfigError("live mode needs these variables in .env: " + ", ".join(missing))
            if not values["AZURE_OPENAI_ENDPOINT"].startswith("https://"):
                raise ConfigError("AZURE_OPENAI_ENDPOINT must start with https://")
            azure = AzureSettings(
                api_key=SecretStr(values["AZURE_OPENAI_API_KEY"]),
                endpoint=values["AZURE_OPENAI_ENDPOINT"],
                api_version=values["AZURE_OPENAI_API_VERSION"],
                deployment=values["AZURE_OPENAI_DEPLOYMENT"],
            )
        cassette = env.get("FLEET_CASSETTE_PATH") or str(
            REPO_ROOT / "var" / "cassettes" / "live.jsonl"
        )
        record = (env.get("FLEET_CASSETTE_RECORD") or "").strip().lower() in ("1", "true", "yes")
        return cls(
            mode=raw_mode,  # type: ignore[arg-type]
            azure=azure,
            cassette_path=Path(cassette),
            record=record,
            call_timeout_s=policy.MODEL_CALL_TIMEOUT_S,
        )


@dataclass(frozen=True)
class ApiSettings:
    session_secret: SecretStr
    operator_passcode: SecretStr | None
    allowed_origins: frozenset[str]
    port: int
    failure_delay_s: float = 0.5  # pause after a failed sign-in; tests set 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ApiSettings:
        env = os.environ if env is None else env
        try:
            port = int(env.get("FLEET_PORT") or 8000)
        except ValueError as exc:
            raise ConfigError("FLEET_PORT must be a number") from exc
        origins = {
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            f"http://127.0.0.1:{port}",
            f"http://localhost:{port}",
        }
        for item in (env.get("FLEET_ALLOWED_ORIGINS") or "").split(","):
            item = item.strip()
            if not item:
                continue
            if "*" in item:
                raise ConfigError("FLEET_ALLOWED_ORIGINS must list exact origins, not wildcards")
            origins.add(item)
        secret = (env.get("FLEET_SESSION_SECRET") or "").strip() or secrets.token_urlsafe(32)
        passcode = (env.get("FLEET_OPERATOR_PASSCODE") or "").strip()
        return cls(
            session_secret=SecretStr(secret),
            operator_passcode=SecretStr(passcode) if passcode else None,
            allowed_origins=frozenset(origins),
            port=port,
        )
