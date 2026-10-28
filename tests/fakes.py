"""In-memory fakes shared by the tests."""

from __future__ import annotations

from typing import Any

from local_paper_ai.ai.schemas import AIDeadlines, AIRetention


class FakeRouter:
    def __init__(
        self, retention: AIRetention | None = None, deadlines: AIDeadlines | None = None
    ) -> None:
        self.retention = retention
        self.deadlines = deadlines
        self.calls: list[tuple[str, str]] = []

    def extract(self, system: str, text: str, schema: type[Any], category: str) -> Any:
        self.calls.append((schema.__name__, category))
        if schema is AIRetention:
            return self.retention
        if schema is AIDeadlines:
            return self.deadlines
        return None
