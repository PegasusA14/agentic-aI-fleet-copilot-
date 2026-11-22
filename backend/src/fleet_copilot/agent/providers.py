"""Model providers. Each is one adapter in `ADAPTERS`; nothing else in the product names a provider.

An adapter knows four things about its provider: which environment variables configure it, how to turn them
into settings (validated, the key held as a `SecretStr`), how to build the LangChain chat model, and how to
sort the errors a call can raise. The gateway, the settings loader, `doctor` and the evaluation entry point
only use that interface, so adding a provider is adding an adapter here.

Azure OpenAI, OpenAI and OpenRouter all speak the OpenAI chat API, so they share the same error rules, and
OpenAI and OpenRouter are the same adapter class with different parameters. Settings never show a key or
an endpoint in `repr`, and every message here names variables, never their values.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

import openai
from pydantic import SecretStr

from fleet_copilot.config import ConfigError

ErrorKind = Literal["retry", "content_filtered", "auth", "unavailable"]
FilterCategory = Literal["jailbreak", "content"]

DEFAULT_PROVIDER = "azure"

# Errors worth another attempt: the provider is busy or the network hiccuped.
_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
    TimeoutError,
)
# What a provider's "refused on content grounds" error looks like (codes, then message words).
_REFUSAL_CODES = ("content_filter", "content_policy", "moderation")
_REFUSAL_WORDS = (
    "content policy",
    "content_policy",
    "content filter",
    "moderation",
    "safety system",
)


# ---- settings ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AzureSettings:
    api_key: SecretStr
    endpoint: str = field(repr=False)
    api_version: str
    deployment: str = field(repr=False)

    @property
    def label(self) -> str:
        """What eval runs call the model by default."""
        return self.deployment


@dataclass(frozen=True)
class OpenAICompatibleSettings:
    api_key: SecretStr
    model: str = field(repr=False)
    base_url: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        return self.model


ProviderSettings = AzureSettings | OpenAICompatibleSettings


# ---- the adapter interface --------------------------------------------------------------------------


@runtime_checkable
class ProviderAdapter(Protocol):
    name: str

    @property
    def required_vars(self) -> tuple[str, ...]:
        """Variables live mode needs. Names only: `doctor` lists them."""

    @property
    def optional_vars(self) -> tuple[str, ...]: ...

    def settings_from_env(self, env: Mapping[str, str]) -> ProviderSettings:
        """Validated settings; raises `ConfigError` naming the variables at fault, never their values."""

    def build_model(self, settings: Any, *, temperature: float, timeout: float) -> Any:
        """The LangChain chat model. No network call happens until it is used."""

    def classify_error(self, exc: BaseException) -> ErrorKind: ...

    def content_filter_category(self, exc: BaseException) -> FilterCategory | None:
        """Why the provider's own filter refused the call, or None when this is not a refusal."""


def _require(env: Mapping[str, str], names: tuple[str, ...], provider: str) -> dict[str, str]:
    values = {name: (env.get(name) or "").strip() for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(
            f"live mode with provider {provider} needs these variables in .env: "
            + ", ".join(missing)
        )
    return values


def _https(name: str, value: str) -> str:
    if not value.startswith("https://") or len(value) <= len("https://"):
        raise ConfigError(f"{name} must start with https://")
    return value


class _OpenAIFamily:
    """Error rules shared by every provider that speaks the OpenAI chat API."""

    def classify_error(self, exc: BaseException) -> ErrorKind:
        if isinstance(exc, _RETRYABLE):
            return "retry"
        if isinstance(exc, openai.APIStatusError):
            if self.content_filter_category(exc) is not None:
                return "content_filtered"
            if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
                return "auth"
        return "unavailable"

    def content_filter_category(self, exc: BaseException) -> FilterCategory | None:
        if not isinstance(exc, openai.APIStatusError):
            return None
        code = str(getattr(exc, "code", None) or "").lower()
        message = str(getattr(exc, "message", "") or "").lower()
        if any(word in code for word in _REFUSAL_CODES) or any(
            word in message for word in _REFUSAL_WORDS
        ):
            return "content"
        return None


# ---- Azure OpenAI -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class AzureAdapter(_OpenAIFamily):
    name: str = "azure"

    @property
    def required_vars(self) -> tuple[str, ...]:
        return (
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_VERSION",
            "AZURE_OPENAI_DEPLOYMENT",
        )

    @property
    def optional_vars(self) -> tuple[str, ...]:
        return ()

    def settings_from_env(self, env: Mapping[str, str]) -> AzureSettings:
        values = _require(env, self.required_vars, self.name)
        return AzureSettings(
            api_key=SecretStr(values["AZURE_OPENAI_API_KEY"]),
            endpoint=_https("AZURE_OPENAI_ENDPOINT", values["AZURE_OPENAI_ENDPOINT"]),
            api_version=values["AZURE_OPENAI_API_VERSION"],
            deployment=values["AZURE_OPENAI_DEPLOYMENT"],
        )

    def build_model(self, settings: AzureSettings, *, temperature: float, timeout: float) -> Any:
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_deployment=settings.deployment,
            azure_endpoint=settings.endpoint,
            api_version=settings.api_version,
            api_key=settings.api_key,
            temperature=temperature,
            timeout=timeout,
            max_retries=0,  # retries are ours: bounded, counted and covered by the turn deadline
        )

    def content_filter_category(self, exc: BaseException) -> FilterCategory | None:
        """Azure's filter result says which rule fired; its Prompt Shields rule is the "jailbreak"."""
        if isinstance(exc, openai.APIStatusError):
            body = exc.body if isinstance(exc.body, dict) else {}
            inner = body.get("innererror") if isinstance(body.get("innererror"), dict) else {}
            if inner.get("code") == "ResponsibleAIPolicyViolation" or getattr(
                exc, "code", None
            ) == ("content_filter"):
                result = inner.get("content_filter_result")
                flags = result if isinstance(result, dict) else {}
                shield = flags.get("jailbreak") if isinstance(flags.get("jailbreak"), dict) else {}
                return (
                    "jailbreak" if shield.get("filtered") or shield.get("detected") else "content"
                )
        return super().content_filter_category(exc)


# ---- OpenAI and the providers that copy its API -----------------------------------------------------


@dataclass(frozen=True)
class OpenAICompatibleAdapter(_OpenAIFamily):
    """A provider that speaks the OpenAI chat API, configured by `<prefix>_API_KEY` and `<prefix>_MODEL`.

    `base_url_var` names an optional variable that overrides the endpoint (https only); without one the
    endpoint is fixed. `app_name_var` names an optional variable for the name sent as the `X-Title`
    header, `app_name` being the default. No other header is added.
    """

    name: str
    prefix: str
    base_url: str
    base_url_var: str | None = None
    app_name_var: str | None = None
    app_name: str | None = None

    @property
    def required_vars(self) -> tuple[str, ...]:
        return (f"{self.prefix}_API_KEY", f"{self.prefix}_MODEL")

    @property
    def optional_vars(self) -> tuple[str, ...]:
        return tuple(v for v in (self.base_url_var, self.app_name_var) if v)

    def settings_from_env(self, env: Mapping[str, str]) -> OpenAICompatibleSettings:
        values = _require(env, self.required_vars, self.name)
        base_url = self.base_url
        if self.base_url_var and (env.get(self.base_url_var) or "").strip():
            base_url = _https(self.base_url_var, (env.get(self.base_url_var) or "").strip())
        headers: dict[str, str] = {}
        title = (env.get(self.app_name_var or "") or "").strip() or self.app_name
        if title:
            if not title.isprintable():
                raise ConfigError(f"{self.app_name_var} must be a single line of text")
            headers["X-Title"] = title
        return OpenAICompatibleSettings(
            api_key=SecretStr(values[f"{self.prefix}_API_KEY"]),
            model=values[f"{self.prefix}_MODEL"],
            base_url=base_url,
            headers=headers,
        )

    def build_model(
        self, settings: OpenAICompatibleSettings, *, temperature: float, timeout: float
    ) -> Any:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            api_key=settings.api_key,
            model=settings.model,
            base_url=settings.base_url,  # always given, so an endpoint in the environment never wins
            default_headers=dict(settings.headers) or None,
            temperature=temperature,
            timeout=timeout,
            max_retries=0,
        )
        if settings.base_url != _OPENAI_BASE_URL:
            # The SDK reads an OpenAI organisation and project from the environment. They belong to
            # that account and must not travel to another endpoint.
            for client in (model.root_client, model.root_async_client):
                client.organization = None
                client.project = None
        return model


_OPENAI_BASE_URL = "https://api.openai.com/v1"

ADAPTERS: dict[str, ProviderAdapter] = {
    "azure": AzureAdapter(),
    "openai": OpenAICompatibleAdapter(
        name="openai", prefix="OPENAI", base_url=_OPENAI_BASE_URL, base_url_var="OPENAI_BASE_URL"
    ),
    "openrouter": OpenAICompatibleAdapter(
        name="openrouter",
        prefix="OPENROUTER",
        base_url="https://openrouter.ai/api/v1",
        app_name_var="OPENROUTER_APP_NAME",
        app_name="Fleet Copilot",
    ),
}


def provider_from_env(env: Mapping[str, str]) -> str:
    """`FLEET_MODEL_PROVIDER`: a registered provider, `DEFAULT_PROVIDER` when blank. Never echoes the value."""
    raw = (env.get("FLEET_MODEL_PROVIDER") or "").strip().lower()
    if not raw:
        return DEFAULT_PROVIDER
    if raw not in ADAPTERS:
        raise ConfigError("FLEET_MODEL_PROVIDER must be one of: " + ", ".join(ADAPTERS))
    return raw
