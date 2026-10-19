"""LLM client protocol for the M6 agent loop.

OllamaClient: real client, connects lazily (import-time safe).
FakeClient: replays a scripted list of responses for tests.
"""
from __future__ import annotations

import json
from types import MappingProxyType
from typing import Any


class OllamaRequestError(RuntimeError):
    """ollama ResponseError augmented with the request context for diagnostics."""

    def __init__(self, message: str, context: dict) -> None:
        super().__init__(message)
        self.context = context  # model_sent, options_sent, n_messages, request_size_chars


class ChatResult:
    """Return value of client.chat()."""

    def __init__(
        self,
        content: str | None,
        tool_calls: list[dict[str, Any]],
        prompt_eval_count: int | None = None,
        eval_count: int | None = None,
    ) -> None:
        self.content = content
        # Each element: {"name": str, "args": dict}
        self.tool_calls = tool_calls
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count


class OllamaClient:
    """Wraps the ollama package. Does NOT connect at import time."""

    def __init__(self, config: dict) -> None:
        # Freeze one level deep: prevents any caller (or library) from
        # silently mutating shared config state across runs.
        self._config = MappingProxyType({
            k: MappingProxyType(v) if isinstance(v, dict) else v
            for k, v in config.items()
        })

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        import ollama  # lazy import — server not contacted at import time
        from ollama import ResponseError
        from agent.config import MODEL_TAG, OPTIONS, THINK

        model_tag = MODEL_TAG
        if not isinstance(model_tag, str) or not model_tag:
            raise RuntimeError(
                f"OllamaClient.chat: model is {model_tag!r} — expected a "
                f"non-empty str equal to the pinned tag; config may be corrupted"
            )

        options_copy = dict(OPTIONS)
        n_messages = len(messages)
        request_size_chars = sum(len(json.dumps(m, default=str)) for m in messages)

        try:
            resp = ollama.chat(
                model=model_tag,
                messages=messages,
                tools=tools or None,
                options=options_copy,  # fresh dict each call; never passes shared ref
                think=THINK,
            )
        except ResponseError as e:
            raise OllamaRequestError(
                f"ollama ResponseError: {e}",
                context={
                    "model_sent": model_tag,
                    "options_sent": options_copy,
                    "n_messages": n_messages,
                    "request_size_chars": request_size_chars,
                },
            ) from e

        msg = resp.message
        tool_calls = [
            {"name": tc.function.name, "args": dict(tc.function.arguments)}
            for tc in (msg.tool_calls or [])
        ]
        return ChatResult(
            content=msg.content,
            tool_calls=tool_calls,
            prompt_eval_count=getattr(resp, "prompt_eval_count", None),
            eval_count=getattr(resp, "eval_count", None),
        )


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
