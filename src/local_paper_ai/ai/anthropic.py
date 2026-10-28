from __future__ import annotations

from typing import Any

import httpx

from local_paper_ai.ai.base import LLMUnavailable, T
from local_paper_ai.config import ProviderCfg

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"


class AnthropicLLM:
    """Claude via the Messages API; structured output through a forced tool call."""

    def __init__(self, cfg: ProviderCfg, http: httpx.Client) -> None:
        self.name = cfg.name
        self.local = cfg.local
        self._cfg = cfg
        self._http = http

    def extract(self, system: str, text: str, schema: type[T]) -> T:
        body: dict[str, Any] = {
            "model": self._cfg.model or DEFAULT_MODEL,
            "max_tokens": 2048,
            "system": system,
            "messages": [{"role": "user", "content": text}],
            "tools": [
                {
                    "name": "emit",
                    "description": "Gib das Ergebnis zurück.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            "tool_choice": {"type": "tool", "name": "emit"},
        }
        headers = {"x-api-key": self._cfg.api_key, "anthropic-version": "2023-06-01"}
        try:
            resp = self._http.post(
                self._cfg.base_url or API_URL,
                json=body,
                headers=headers,
                timeout=self._cfg.timeout_s,
            )
            resp.raise_for_status()
            block = next(b for b in resp.json()["content"] if b.get("type") == "tool_use")
            return schema.model_validate(block["input"])
        except (httpx.HTTPError, StopIteration, KeyError, TypeError, ValueError) as exc:
            raise LLMUnavailable(f"{self.name}: {exc}") from exc
