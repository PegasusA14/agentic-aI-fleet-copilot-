from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import SecretStr

if TYPE_CHECKING:
    from fleet_copilot.agent.providers import ProviderSettings

# backend/src/fleet_copilot/config.py -> repository root is three levels above the package dir.
REPO_ROOT = Path(__file__).resolve().parents[3]

EXPECTED_DATASET_SHA256 = "fb0ee31ffeeb731b919fc4d5124098c9c5b68cf3fba35eed5092a9f73655c4de"
PINNED_AS_OF = "2026-06-12T11:00:00Z"

ModelMode = Literal["replay", "live"]


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


def _temperature(name: str, raw: str | None) -> float | None:
    """A temperature variable: a number from 0 to 1, None when blank. The message never repeats the value."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        raise ConfigError(f"{name} must be a number from 0 to 1") from None
    if not 0.0 <= value <= 1.0:  # also false for nan
        raise ConfigError(f"{name} must be a number from 0 to 1")
    return value


def _reply_model(raw: str | None) -> str | None:
    """`FLEET_REPLY_MODEL`: a model or deployment name on the same provider, None when blank."""
    text = (raw or "").strip()
    if text and not text.isprintable():
        raise ConfigError("FLEET_REPLY_MODEL must be a single line of text")
    return text or None


@dataclass(frozen=True)
class ModelSettings:
    mode: ModelMode
    cassette_path: Path
    record: bool
    call_timeout_s: float
    temperature: float = 0.0
    # A label for the model behind the provider, recorded with eval runs. It never picks the model.
    model_name: str | None = field(default=None, repr=False)
    # The model (an Azure deployment) for the reply step, on the same provider. None means the main model.
    reply_model: str | None = field(default=None, repr=False)
    # None means the main temperature.
    reply_temperature: float | None = None
    provider: str = "azure"  # a key of `agent.providers.ADAPTERS`
    provider_settings: ProviderSettings | None = None  # set in live mode, validated by the adapter

    @property
    def reply_model_name(self) -> str | None:
        """The label of the model that writes replies: the reply model, else the main model's label."""
        return self.reply_model or self.model_name

    @property
    def effective_reply_temperature(self) -> float:
        return self.temperature if self.reply_temperature is None else self.reply_temperature

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ModelSettings:
        env = os.environ if env is None else env
        raw_mode = (env.get("FLEET_MODEL_MODE") or "replay").strip().lower()
        if raw_mode not in ("replay", "live"):
            raise ConfigError("FLEET_MODEL_MODE must be 'replay' or 'live'")
        from fleet_copilot import (
            policy,
        )  # local imports: policy has no dependencies and the providers import this module
        from fleet_copilot.agent.providers import ADAPTERS, provider_from_env

        temperature = _temperature("FLEET_MODEL_TEMPERATURE", env.get("FLEET_MODEL_TEMPERATURE"))
        reply_temperature = _temperature(
            "FLEET_REPLY_TEMPERATURE", env.get("FLEET_REPLY_TEMPERATURE")
        )
        reply_model = _reply_model(env.get("FLEET_REPLY_MODEL"))
        label = (env.get("FLEET_MODEL_NAME") or "").strip() or None
        provider = provider_from_env(env)
        credentials = ADAPTERS[provider].settings_from_env(env) if raw_mode == "live" else None
        record = (env.get("FLEET_CASSETTE_RECORD") or "").strip().lower() in ("1", "true", "yes")
        # Replay reads the committed evaluation cassette, so a checkout answers its recorded questions with
        # no key. Live mode and recording default to a git-ignored file, never the committed one.
        default = (
            REPO_ROOT / "evals" / "cassettes" / "eval.jsonl"
            if raw_mode == "replay" and not record
            else REPO_ROOT / "var" / "cassettes" / "live.jsonl"
        )
        cassette = env.get("FLEET_CASSETTE_PATH") or str(default)
        return cls(
            mode=raw_mode,  # type: ignore[arg-type]
            cassette_path=Path(cassette),
            record=record,
            call_timeout_s=policy.MODEL_CALL_TIMEOUT_S,
            temperature=0.0 if temperature is None else temperature,
            reply_temperature=reply_temperature,
            reply_model=reply_model,
            model_name=label or (credentials.label if credentials else None),
            provider=provider,
            provider_settings=credentials,
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
