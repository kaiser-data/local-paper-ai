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
