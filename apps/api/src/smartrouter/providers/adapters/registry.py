"""Adapter lookup by provider kind. Adding a provider = one adapter file + one line here."""

from __future__ import annotations

import httpx

from smartrouter.providers.adapters.anthropic import AnthropicAdapter
from smartrouter.providers.adapters.base import ProviderAdapter
from smartrouter.providers.adapters.openai import OpenAIAdapter

# A TokenShield gateway speaks the OpenAI wire format; SmartRouter additionally reads its
# cost headers and reacts to its budget errors (see gateway/service.py).
PROVIDER_KINDS = ("openai", "anthropic", "tokenshield")


class AdapterRegistry:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._adapters: dict[str, ProviderAdapter] = {
            "openai": OpenAIAdapter(client),
            "anthropic": AnthropicAdapter(client),
            "tokenshield": OpenAIAdapter(client),
        }

    def get(self, kind: str) -> ProviderAdapter:
        try:
            return self._adapters[kind]
        except KeyError:
            raise ValueError(f"unknown provider kind: {kind}") from None
