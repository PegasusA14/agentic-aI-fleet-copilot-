"""Pick the model gateway from settings."""

from __future__ import annotations

from fleet_copilot.agent.azure import ChatGateway, build_azure_model
from fleet_copilot.agent.cassette import CassetteGateway
from fleet_copilot.agent.llm import LLMGateway
from fleet_copilot.config import ModelSettings


def build_gateway(settings: ModelSettings) -> LLMGateway:
    if settings.mode == "replay":
        return CassetteGateway(settings.cassette_path)
    if settings.azure is None:
        raise ValueError("live mode needs Azure settings")
    live = ChatGateway(
        build_azure_model(settings.azure, settings.call_timeout_s),
        call_timeout_s=settings.call_timeout_s,
    )
    return CassetteGateway(settings.cassette_path, inner=live) if settings.record else live
