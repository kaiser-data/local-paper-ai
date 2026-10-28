### Task 5: Provider-agnostic AI adapter with privacy lock

**Files:**
- Create: `src/local_paper_ai/ai/__init__.py` (empty docstring only), `ai/base.py`, `ai/schemas.py`, `ai/prompts.py`, `ai/openai_compat.py`, `ai/anthropic.py`, `ai/router.py`
- Test: `tests/test_ai.py`

**Interfaces:**
- Consumes: `ProviderCfg`, `AICfg` (Task 2), `Document`, `VerdictKind` (Task 2).
- Produces:
  - `ai.base`: `T` (TypeVar bound to BaseModel), `LLMUnavailable(Exception)`, `Protocol LLM { name: str; local: bool; extract(system: str, text: str, schema: type[T]) -> T }`
  - `ai.schemas`: `AIRetention(category, verdict: VerdictKind, keep_years: int|None, quote, reason)`, `AIDeadline(due: date, label, quote)`, `AIDeadlines(items: list[AIDeadline])`
  - `ai.prompts`: `RETENTION_SYSTEM`, `DEADLINES_SYSTEM`, `document_text(doc) -> str`
  - `ai.openai_compat.OpenAICompatible(cfg, http: httpx.Client, free_mb: Callable[[], int])`
  - `ai.anthropic.AnthropicLLM(cfg, http)`
  - `ai.router`:
    - `Protocol Extractor { extract(system, text, schema: type[T], category: str) -> T | None }`
    - `Router(providers: Sequence[LLM], local_only: Iterable[str])`, which has a `.providers` list and implements `Extractor`
    - `build_router(cfg: AICfg, http, free_mb) -> Router`

- [x] **Step 1: Write the failing test** `tests/test_ai.py`

```python
import json

import httpx
import pytest
import respx
from pydantic import BaseModel

from local_paper_ai.ai.anthropic import AnthropicLLM
from local_paper_ai.ai.base import LLMUnavailable
from local_paper_ai.ai.openai_compat import OpenAICompatible
from local_paper_ai.ai.router import Router, build_router
from local_paper_ai.config import AICfg, ProviderCfg


class Out(BaseModel):
    answer: str


def _cfg(**kw: object) -> ProviderCfg:
    return ProviderCfg(
        name="ollama", kind="openai_compatible", base_url="http://llm/v1", model="phi4-mini", **kw  # type: ignore[arg-type]
    )


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_openai_compatible_parses_json(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("http://llm/v1/chat/completions").mock(
        return_value=_chat('{"answer": "ja"}')
    )
    llm = OpenAICompatible(_cfg(), httpx.Client(), free_mb=lambda: 8000)
    assert llm.extract("sys", "text", Out).answer == "ja"
    body = json.loads(route.calls.last.request.content)
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["messages"][0] == {"role": "system", "content": "sys"}


def test_openai_compatible_strips_code_fences(respx_mock: respx.MockRouter) -> None:
    fence = "`" * 3
    respx_mock.post("http://llm/v1/chat/completions").mock(
        return_value=_chat(f'{fence}json\n{{"answer": "ja"}}\n{fence}')
    )
    llm = OpenAICompatible(_cfg(), httpx.Client(), free_mb=lambda: 8000)
    assert llm.extract("s", "t", Out).answer == "ja"


def test_refuses_when_free_ram_below_budget() -> None:
    llm = OpenAICompatible(_cfg(model_budget_mb=3500), httpx.Client(), free_mb=lambda: 1000)
    with pytest.raises(LLMUnavailable, match="free RAM"):
        llm.extract("s", "t", Out)


def test_invalid_output_is_unavailable(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("http://llm/v1/chat/completions").mock(return_value=_chat("not json"))
    llm = OpenAICompatible(_cfg(), httpx.Client(), free_mb=lambda: 8000)
    with pytest.raises(LLMUnavailable):
        llm.extract("s", "t", Out)


def test_ollama_unload_after_call(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("http://llm/v1/chat/completions").mock(return_value=_chat('{"answer":"x"}'))
    unload = respx_mock.post("http://llm/api/generate").mock(return_value=httpx.Response(200))
    llm = OpenAICompatible(_cfg(ollama_unload=True), httpx.Client(), free_mb=lambda: 8000)
    llm.extract("s", "t", Out)
    assert json.loads(unload.calls.last.request.content) == {"model": "phi4-mini", "keep_alive": 0}


def test_anthropic_tool_use(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"content": [{"type": "tool_use", "name": "emit", "input": {"answer": "ja"}}]}
        )
    )
    cfg = ProviderCfg(name="claude", kind="anthropic", api_key="k", local=False)
    assert AnthropicLLM(cfg, httpx.Client()).extract("s", "t", Out).answer == "ja"
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "k"
    assert json.loads(request.content)["tool_choice"] == {"type": "tool", "name": "emit"}


class FakeLLM:
    def __init__(self, name: str, local: bool, fail: bool = False) -> None:
        self.name, self.local, self.fail, self.calls = name, local, fail, 0

    def extract(self, system: str, text: str, schema: type[Out]) -> Out:
        self.calls += 1
        if self.fail:
            raise LLMUnavailable(self.name)
        return schema(answer=self.name)


def test_cloud_skipped_for_local_only_category() -> None:
    cloud = FakeLLM("cloud", local=False)
    router = Router([cloud], {"tax", "unknown"})
    assert router.extract("s", "t", Out, "tax") is None
    assert router.extract("s", "t", Out, "unknown") is None
    assert cloud.calls == 0


def test_cloud_allowed_for_other_category() -> None:
    router = Router([FakeLLM("cloud", local=False)], {"tax"})
    result = router.extract("s", "t", Out, "household")
    assert result is not None and result.answer == "cloud"


def test_falls_back_to_next_provider() -> None:
    router = Router([FakeLLM("a", True, fail=True), FakeLLM("b", True)], [])
    result = router.extract("s", "t", Out, "tax")
    assert result is not None and result.answer == "b"


def test_build_router_skips_none_kind() -> None:
    cfg = AICfg(providers=[ProviderCfg(name="n", kind="none"), _cfg()])
    router = build_router(cfg, httpx.Client(), free_mb=lambda: 0)
    assert [p.name for p in router.providers] == ["ollama"]
```

- [x] **Step 2: Run it**

Run: `uv run pytest tests/test_ai.py -v`
Expected: FAIL with `No module named 'local_paper_ai.ai'`

- [x] **Step 3: Implement**

`src/local_paper_ai/ai/__init__.py`:
```python
"""Provider-agnostic AI adapter."""
```

`src/local_paper_ai/ai/base.py`:
```python
from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    """The provider could not answer: down, over its memory budget, or invalid output."""


class LLM(Protocol):
    name: str
    local: bool

    def extract(self, system: str, text: str, schema: type[T]) -> T: ...
```

`src/local_paper_ai/ai/schemas.py`:
```python
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from local_paper_ai.models import VerdictKind

Category = Literal[
    "tax", "health", "identity", "finance", "legal", "property", "vehicle", "education",
    "pension", "purchase", "household", "insurance", "other",
]


class AIRetention(BaseModel):
    category: Category
    verdict: VerdictKind
    keep_years: int | None = Field(default=None, ge=1, le=30)
    quote: str
    reason: str


class AIDeadline(BaseModel):
    due: date
    label: str
    quote: str


class AIDeadlines(BaseModel):
    items: list[AIDeadline] = Field(default_factory=list)
```

`src/local_paper_ai/ai/prompts.py`:
```python
from __future__ import annotations

from local_paper_ai.models import Document

MAX_CHARS = 12_000

RETENTION_SYSTEM = """\
Du prüfst ein eingescanntes Dokument aus einem deutschen Privathaushalt.
Entscheide, ob das Papier-Original nach dem Scannen weggeworfen werden darf.
- keep_original: das Original hat rechtlichen Wert (Urkunden, notarielle Verträge,
  Zeugnisse, Fahrzeugbrief, Kredit- und Bürgschaftsverträge, Rentennachweise).
- keep_until: bis zu einer Frist aufbewahren; gib keep_years an
  (z. B. Steuerbescheid 10, Handwerkerrechnung 2, Kaufbeleg mit Garantie 2).
- may_shred: die digitale Kopie reicht.
Im Zweifel keep_original.
"quote" MUSS ein wörtliches Zitat (mindestens 8 Zeichen) aus dem Dokument sein,
das die Entscheidung belegt. "reason" ist ein kurzer deutscher Satz.
Antworte nur mit JSON nach dem Schema."""

DEADLINES_SYSTEM = """\
Du suchst in einem eingescannten Brief nach Fristen und Zahlungszielen
(Einspruchsfrist, Widerspruchsfrist, Zahlungsziel, Kündigungsfrist).
Rechne relative Fristen ("innerhalb eines Monats nach Bekanntgabe") ab dem
Dokumentdatum aus. Gib je Frist "due" (YYYY-MM-DD), ein kurzes deutsches "label"
und ein wörtliches Zitat "quote" aus dem Dokument an.
Keine Frist gefunden: "items" ist eine leere Liste. Antworte nur mit JSON nach dem Schema."""


def document_text(doc: Document) -> str:
    return (
        f"Titel: {doc.title}\n"
        f"Dokumentdatum: {doc.created.isoformat()}\n"
        f"Absender: {doc.correspondent or 'unbekannt'}\n\n"
        f"{doc.content[:MAX_CHARS]}"
    )
```

`src/local_paper_ai/ai/openai_compat.py`:
```python
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
```

`src/local_paper_ai/ai/anthropic.py`:
```python
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
```

`src/local_paper_ai/ai/router.py`:
```python
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
```

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_ai.py -v && uv run mypy`
Expected: `10 passed`, mypy success. If mypy complains that `FakeLLM` in the tests doesn't satisfy `LLM`, that is fine: tests are not type-checked (`packages = ["local_paper_ai"]`).

- [x] **Step 5: Commit and push** with the message `feat: provider-agnostic AI adapter with privacy lock` (plus the trailer).
