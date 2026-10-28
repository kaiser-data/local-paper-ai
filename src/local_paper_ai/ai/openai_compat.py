from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import Any

import httpx

from local_paper_ai.ai.base import LLMUnavailable, T
from local_paper_ai.config import ProviderCfg

_FENCE = "`" * 3


def _strip_fences(content: str) -> str:
    text = content.strip()
    if text.startswith(_FENCE):
        text = text.removeprefix(_FENCE).removeprefix("json").removesuffix(_FENCE)
    return text.strip()


class OpenAICompatible:
    """Any /v1/chat/completions endpoint: Ollama, llama.cpp, LiteLLM, vLLM, OpenRouter, OpenAI."""

    def __init__(self, cfg: ProviderCfg, http: httpx.Client, free_mb: Callable[[], int]) -> None:
        self.name = cfg.name
        self.local = cfg.local
        self._cfg = cfg
        self._http = http
        self._free_mb = free_mb
        self._base = cfg.base_url.rstrip("/")

    def extract(self, system: str, text: str, schema: type[T]) -> T:
        budget = self._cfg.model_budget_mb
        if budget and self._free_mb() < budget:
            raise LLMUnavailable(f"{self.name}: less than {budget} MB free RAM")
        body: dict[str, Any] = {
            "model": self._cfg.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema()},
            },
        }
        headers = {"Authorization": f"Bearer {self._cfg.api_key}"} if self._cfg.api_key else {}
        try:
            resp = self._http.post(
                f"{self._base}/chat/completions",
                json=body,
                headers=headers,
                timeout=self._cfg.timeout_s,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return schema.model_validate_json(_strip_fences(content))
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMUnavailable(f"{self.name}: {exc}") from exc
        finally:
            if self._cfg.ollama_unload:
                self._unload()

    def _unload(self) -> None:
        """Free the model's memory right away (Ollama: keep_alive 0)."""
        root = self._base.removesuffix("/v1")
        with suppress(httpx.HTTPError):
            self._http.post(
                f"{root}/api/generate",
                json={"model": self._cfg.model, "keep_alive": 0},
                timeout=30,
            )
