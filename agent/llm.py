"""LLM client protocol for the M6 agent loop.

OllamaClient: real client, connects lazily (import-time safe).
FakeClient: replays a scripted list of responses for tests.
"""
from __future__ import annotations

from typing import Any


class ChatResult:
    """Return value of client.chat()."""

    def __init__(self, content: str | None, tool_calls: list[dict[str, Any]]) -> None:
        self.content = content
        # Each element: {"name": str, "args": dict}
        self.tool_calls = tool_calls


class OllamaClient:
    """Wraps the ollama package. Does NOT connect at import time."""

    def __init__(self, config: dict) -> None:
        self._config = config

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        import ollama  # lazy import — server not contacted at import time
        resp = ollama.chat(
            model=self._config["model_tag"],
            messages=messages,
            tools=tools or None,
            options=self._config.get("options"),
            think=self._config.get("think", False),
        )
        msg = resp.message
        tool_calls = [
            {"name": tc.function.name, "args": dict(tc.function.arguments)}
            for tc in (msg.tool_calls or [])
        ]
        return ChatResult(content=msg.content, tool_calls=tool_calls)


class FakeClient:
    """Replays a scripted list of responses sequentially. Thread-unsafe.

    Each scripted item is a dict with optional keys:
        content: str | None
        tool_calls: list[{"name": str, "args": dict}]
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        if self._idx >= len(self._responses):
            raise RuntimeError(
                f"FakeClient exhausted after {self._idx} call(s)"
            )
        r = self._responses[self._idx]
        self._idx += 1
        return ChatResult(
            content=r.get("content"),
            tool_calls=r.get("tool_calls", []),
        )

    @property
    def calls_made(self) -> int:
        return self._idx
