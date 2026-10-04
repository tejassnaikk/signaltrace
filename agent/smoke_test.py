"""M6 kickoff smoke test - confirms tool-calling works through the `ollama`
Python package (not curl) against the pinned local model (agent/config.py).

Not the agent loop - PLAN.md 4.1's three-tool contract, the forbidden
list, and the answer schema are not built yet. This only checks the
mechanical path (prompt + one tool schema -> one correct tool call) works
end to end before any of that is built on top of it.

The prompt below is authored fresh for this script - this session has no
record of the exact wording used in the standalone curl smoke test the
task referenced (it ran outside this session), only its reported result
(correct tool call, 20.3 tok/s, 200s cold load). Same scenario (2017-02-06,
device dimension, conversion_rate), not a byte-for-byte reproduction.
"""
from __future__ import annotations

import json
import sys

import ollama

from agent.config import MODEL_TAG, OPTIONS, THINK, config

TEST_DIMENSION_TOOL = {
    "type": "function",
    "function": {
        "name": "test_dimension",
        "description": (
            "Test every segment of one dimension for one metric. Returns, "
            "per segment, its verdict, basis, eligibility, contribution, "
            "q-value, and evidence-ledger entry id, plus the mix row for "
            "ratio metrics (PLAN.md Section 4.1)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {
                    "type": "string",
                    "enum": ["sessions", "conversion_rate", "aov"],
                },
                "dimension": {
                    "type": "string",
                    "enum": ["channel", "device", "country"],
                },
            },
            "required": ["metric", "dimension"],
        },
    },
}

PROMPT = (
    "You are investigating a business metric anomaly for 2017-02-06. "
    "conversion_rate deviated significantly from its expected baseline on "
    "that date. You have one tool available, test_dimension(metric, "
    "dimension), which tests every segment of one dimension for one "
    "metric and returns each segment's verdict and contribution. "
    "Begin your investigation by testing the device dimension for "
    "conversion_rate."
)


def main() -> None:
    messages = [{"role": "user", "content": PROMPT}]

    resp = ollama.chat(
        model=MODEL_TAG,
        messages=messages,
        tools=[TEST_DIMENSION_TOOL],
        options=OPTIONS,
        think=THINK,
    )

    tool_calls = resp.message.tool_calls or []
    print(f"tool_calls: {len(tool_calls)}")
    for tc in tool_calls:
        print(f"  {tc.function.name}({dict(tc.function.arguments)})")

    assert len(tool_calls) == 1, f"expected exactly 1 tool call, got {len(tool_calls)}"
    call = tool_calls[0]
    assert call.function.name == "test_dimension", (
        f"expected test_dimension, got {call.function.name!r}")
    args = dict(call.function.arguments)
    assert args.get("metric") == "conversion_rate", (
        f"expected metric=conversion_rate, got {args.get('metric')!r}")
    assert args.get("dimension") == "device", (
        f"expected dimension=device, got {args.get('dimension')!r}")

    load_s = (resp.load_duration or 0) / 1e9
    eval_s = (resp.eval_duration or 0) / 1e9
    tok_per_s = (resp.eval_count / eval_s) if eval_s > 0 else float("nan")

    print(f"\nPASS: exactly one correct tool call "
         f"(test_dimension, metric=conversion_rate, dimension=device)")
    print(f"load_duration: {load_s:.1f}s")
    print(f"eval: {resp.eval_count} tokens in {eval_s:.2f}s = {tok_per_s:.1f} tok/s")
    print(f"\nconfig used: {json.dumps(config(), indent=2)}")


if __name__ == "__main__":
    main()
