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
        name="ollama",
        kind="openai_compatible",
        base_url="http://llm/v1",
        model="phi4-mini",
        **kw,  # type: ignore[arg-type]
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
