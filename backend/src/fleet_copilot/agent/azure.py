"""Kept so older imports keep working: the gateway is provider-neutral and lives in `chat_gateway`."""

from fleet_copilot.agent.chat_gateway import ChatGateway, ModelUnavailable

__all__ = ["ChatGateway", "ModelUnavailable"]
