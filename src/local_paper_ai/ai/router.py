from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Protocol

import httpx

from local_paper_ai.ai.anthropic import AnthropicLLM
from local_paper_ai.ai.base import LLM, LLMUnavailable, T
from local_paper_ai.ai.openai_compat import OpenAICompatible
from local_paper_ai.config import AICfg

log = logging.getLogger(__name__)


class Extractor(Protocol):
    def extract(self, system: str, text: str, schema: type[T], category: str) -> T | None: ...


class Router:
    """Tries providers in order. Cloud providers never see local-only categories."""

    def __init__(self, providers: Sequence[LLM], local_only: Iterable[str]) -> None:
        self.providers = list(providers)
        self._local_only = set(local_only)

    def extract(self, system: str, text: str, schema: type[T], category: str) -> T | None:
        for provider in self.providers:
            if not provider.local and category in self._local_only:
                log.debug("skip %s for local-only category %s", provider.name, category)
                continue
            try:
                return provider.extract(system, text, schema)
            except LLMUnavailable as exc:
                log.warning("AI provider unavailable: %s", exc)
        return None


def build_router(cfg: AICfg, http: httpx.Client, free_mb: Callable[[], int]) -> Router:
    providers: list[LLM] = []
    for p in cfg.providers:
        if p.kind == "openai_compatible":
            providers.append(OpenAICompatible(p, http, free_mb))
        elif p.kind == "anthropic":
            providers.append(AnthropicLLM(p, http))
    return Router(providers, cfg.local_only)
