"""Agent loop for M6 (PLAN.md Section 4.1).

run_agent(session, client, budget=14) -> dict

The loop exposes four tools to the model: get_factor_decomposition,
test_dimension, get_ledger_summary, and submit_answer.  submit_answer
terminates the loop.  The harness hydrates metric/dimension/segment/kind/
verdict/basis from the session's ledger; the agent only supplies entry_id,
direction, and strength.

Does not import harness-side packages.
"""
from __future__ import annotations

import json
import traceback as _tb
from typing import Any

from agent.prompt import SYSTEM_PROMPT
from agent.tools import _row_direction
from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS

_ALLOWED_METRICS = list(REVENUE_FACTORS)
_ALLOWED_DIMENSIONS = list(DIMENSIONS)
_PATTERNS = ("localized", "broad", "mix", "none")
_DIRECTIONS = ("up", "down")
_STRENGTHS = ("strong", "plausible", "untested", "exact")

# Strength word → the ledger verdict it must match
_STRENGTH_TO_VERDICT = {
    "strong": "strong",
    "plausible": "plausible",
    "untested": "material_untested",
    "exact": "arithmetic",
}

# ------------------------------------------------------------------ #
# Tool schemas (JSON / function-calling format)                       #
# ------------------------------------------------------------------ #

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_factor_decomposition",
            "description": (
                "Revenue factor table: actual, expected, pct_change, and "
                "log-space contribution for sessions, conversion_rate, and aov."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "test_dimension",
            "description": (
                "Test every segment of one dimension for one metric. Returns "
                "segment, kind, direction, verdict, contribution, q_value, and "
                "entry_id for each non-immaterial row. Immaterial rows "
                "(contribution < 5%) are collapsed to a count note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "enum": _ALLOWED_METRICS,
                    },
                    "dimension": {
                        "type": "string",
                        "enum": _ALLOWED_DIMENSIONS,
                    },
                },
                "required": ["metric", "dimension"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ledger_summary",
            "description": (
                "Compact list of non-immaterial evidence entries recorded so far: "
                "entry_id and verdict only. Use to review coverage and find "
                "entry_ids for claims."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": (
                "Submit your final answer. Terminates the investigation. "
                "Each claim cites one entry_id from the ledger; the harness "
                "looks up metric/dimension/segment/kind/verdict/basis automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "enum": list(_PATTERNS),
                        "description": "Overall anomaly pattern.",
                    },
                    "claims": {
                        "type": "array",
                        "description": (
                            "Ranked list of supporting evidence. "
                            "Empty list is valid (use for pattern=none or broad)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "entry_id": {
                                    "type": "string",
                                    "description": "Ledger entry_id from test_dimension output.",
                                },
                                "direction": {
                                    "type": "string",
                                    "enum": list(_DIRECTIONS),
                                    "description": "Whether this entry moved up or down.",
                                },
                                "strength": {
                                    "type": "string",
                                    "enum": list(_STRENGTHS),
                                    "description": (
                                        "strong/plausible for significance-tested rows; "
                                        "untested for aov segments or ineligible rows; "
                                        "exact for arithmetic mix rows."
                                    ),
                                },
                            },
                            "required": ["entry_id", "direction", "strength"],
                        },
                    },
                    "narrative": {
                        "type": "string",
                        "description": "Free-text explanation of the anomaly.",
                    },
                },
                "required": ["pattern", "claims", "narrative"],
            },
        },
    },
]


# ------------------------------------------------------------------ #
# Internal helpers                                                    #
# ------------------------------------------------------------------ #

def _validate_submit_schema(args: Any) -> str | None:
    """Return an error string if args violates the submit_answer schema, else None."""
    if not isinstance(args, dict):
        return "submit_answer args must be a JSON object"
    if "pattern" not in args:
        return "missing required field 'pattern'"
    if args["pattern"] not in _PATTERNS:
        return f"'pattern' must be one of {_PATTERNS}; got {args['pattern']!r}"
    if "claims" not in args:
        return "missing required field 'claims'"
    if not isinstance(args["claims"], list):
        return "'claims' must be an array"
    for i, c in enumerate(args["claims"]):
        if not isinstance(c, dict):
            return f"claims[{i}] must be an object"
        for field in ("entry_id", "direction", "strength"):
            if field not in c:
                return f"claims[{i}] missing required field {field!r}"
        # direction/strength values that are outside the allowed enums are
        # NOT structural errors: they are rejected per-claim in _check_claims
        # with reason invalid_direction / invalid_strength.
    if "narrative" not in args:
        return "missing required field 'narrative'"
    return None


def _check_claims(
    raw_claims: list[dict], ledger
) -> tuple[list[dict], list[dict]]:
    """Validate each claim against the harness ledger.

    Returns (accepted, rejected).  Each accepted item is a plain dict with
    all fields hydrated from the ledger; rejected items include a 'reason'.
    The loop never raises here — bad claims are silently removed.
    """
    by_id = {e.evidence_id: e for e in ledger.entries}
    accepted: list[dict] = []
    rejected: list[dict] = []

    for c in raw_claims:
        entry_id = c.get("entry_id", "")
        direction = c.get("direction", "")
        strength = c.get("strength", "")

        if direction not in _DIRECTIONS:
            rejected.append({**c, "reason": f"invalid_direction: {direction!r} not in {list(_DIRECTIONS)}"})
            continue
        if strength not in _STRENGTH_TO_VERDICT:
            rejected.append({**c, "reason": f"invalid_strength: {strength!r} not in {list(_STRENGTH_TO_VERDICT)}"})
            continue

        entry = by_id.get(entry_id)
        if entry is None:
            rejected.append({**c, "reason": f"no ledger entry {entry_id!r}"})
            continue

        expected_dir = _row_direction(entry.actual, entry.expected)
        if direction != expected_dir:
            rejected.append({
                **c, "reason":
                f"direction {direction!r} contradicts entry sign "
                f"(actual={entry.actual}, expected={entry.expected} → {expected_dir!r})"
            })
            continue

        required_verdict = _STRENGTH_TO_VERDICT[strength]
        if entry.verdict != required_verdict:
            rejected.append({
                **c, "reason":
                f"strength {strength!r} requires verdict {required_verdict!r} "
                f"but entry has {entry.verdict!r}"
            })
            continue

        accepted.append({
            "entry_id": entry_id,
            "metric": entry.metric,
            "dimension": entry.dimension,
            "segment": entry.segment,
            "kind": "within" if entry.kind == "segment" else entry.kind,
            "direction": direction,
            "strength": entry.verdict,   # actual verdict string, not agent's word
            "basis": entry.basis,
            "evidence_ids": [entry_id],
        })

    return accepted, rejected


def _dispatch(name: str, args: dict, session) -> Any:
    """Call the appropriate ToolSession method and return a JSON-safe value."""
    if name == "get_factor_decomposition":
        return session.get_factor_decomposition()
    if name == "test_dimension":
        return session.test_dimension(
            args.get("metric", ""), args.get("dimension", "")
        )
    if name == "get_ledger_summary":
        return session.get_ledger_summary()
    return {"error": f"unknown tool {name!r}"}


def _tool_msg(name: str, content: Any) -> dict:
    return {
        "role": "tool",
        "content": json.dumps(content, default=str),
        "tool_name": name,
    }


# ------------------------------------------------------------------ #
# Main loop                                                           #
# ------------------------------------------------------------------ #

_CONTEXT_GUARD_TOKENS = 13000  # n_ctx=16384 confirmed; max compact transcript 6828 tokens


def run_agent(session, client, budget: int = 14,
              context_limit: int = _CONTEXT_GUARD_TOKENS) -> dict:
    """Run the evidence-constrained agent loop.

    Budget counts ALL tool calls including submit_answer and repair attempts.
    The exhaustive path (9 test_dimension + get_factor_decomposition +
    get_ledger_summary) plus 2 malformed repairs + 1 valid submit = 14 calls,
    which is exactly the default budget.

    Returns a dict with:
        answer      : plain dict (pattern, claims, primary, narrative) or None
        trace       : list of event dicts
        tool_call_counts : session._call_counts_snapshot()
        status      : "ok" | "failed_budget" | "failed_malformed" | "failed_context" | "failed_error"
        n_rejected  : int — claims removed by the checker
        rejected    : list of rejected claim dicts with "reason"
        error       : str (traceback) for failed_error, else None
    """
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Investigate {session._date:%Y-%m-%d}."},
    ]

    trace: list[dict] = []
    tc_total = 0
    malformed_count = 0
    n_rejected = 0
    rejected_all: list[dict] = []
    answer: dict | None = None
    status: str | None = None

    try:
        while True:
            # Context guard: fail loudly before sending a request that would
            # exceed the model's context.  3 chars ≈ 1 token (pessimistic).
            _ctx_chars = sum(len(json.dumps(m, default=str)) for m in messages)
            if _ctx_chars // 3 > context_limit:
                status = "failed_context"
                break

            resp = client.chat(messages, _TOOLS)

            # Record assistant turn in messages
            asst_msg: dict = {"role": "assistant", "content": resp.content or ""}
            if resp.tool_calls:
                asst_msg["tool_calls"] = [
                    {"function": {"name": tc["name"], "arguments": tc["args"]}}
                    for tc in resp.tool_calls
                ]
            messages.append(asst_msg)
            trace.append({
                "event": "llm_response",
                "content": resp.content,
                "tool_calls": list(resp.tool_calls),
                "prompt_eval_count": resp.prompt_eval_count,
                "eval_count": resp.eval_count,
            })

            if not resp.tool_calls:
                # No tool call — nudge the model back on track
                nudge = (
                    "Please call one of the available tools, "
                    "or call submit_answer to finish."
                )
                messages.append({"role": "user", "content": nudge})
                trace.append({"event": "nudge"})
                continue

            broke_out = False
            for tc in resp.tool_calls:
                name = tc["name"]
                args = tc["args"]

                if name == "submit_answer":
                    tc_total += 1
                    if tc_total > budget:
                        status = "failed_budget"
                        broke_out = True
                        break

                    schema_error = _validate_submit_schema(args)
                    if schema_error:
                        malformed_count += 1
                        if malformed_count > 2:
                            status = "failed_malformed"
                            broke_out = True
                            break
                        # Return the error to the model for repair
                        messages.append(_tool_msg("submit_answer", {
                            "error": schema_error,
                            "hint": "Fix the error and call submit_answer again.",
                        }))
                        trace.append({"event": "malformed_submit", "error": schema_error})
                        broke_out = True  # restart outer while without finishing
                        break

                    # Valid schema — apply semantic checker
                    accepted, bad = _check_claims(args["claims"], session._harness_ledger())
                    n_rejected += len(bad)
                    rejected_all.extend(bad)

                    answer = {
                        "pattern": args["pattern"],
                        "claims": accepted,
                        "primary": accepted[:3],
                        "narrative": args.get("narrative", ""),
                    }
                    status = "ok"
                    trace.append({
                        "event": "submit_answer",
                        "pattern": args["pattern"],
                        "n_claims": len(args["claims"]),
                        "n_accepted": len(accepted),
                        "n_rejected": len(bad),
                    })
                    broke_out = True
                    break

                else:
                    # Regular tool
                    tc_total += 1
                    if tc_total > budget:
                        status = "failed_budget"
                        broke_out = True
                        break

                    result = _dispatch(name, args, session)
                    messages.append(_tool_msg(name, result))
                    trace.append({"event": "tool_call", "name": name, "args": args})

            if status in ("ok", "failed_budget", "failed_malformed",
                          "failed_context", "failed_error"):
                break
            # If broke_out is True but status is None, the outer loop continues
            # (e.g. after a malformed submit repair was sent back)

    except Exception:
        status = "failed_error"
        err = _tb.format_exc()
        # Record what the message list looked like when the exception fired —
        # lets us identify which request to Ollama triggered the error.
        last_msg = messages[-1] if messages else {}
        trace.append({
            "event": "error",
            "n_messages": len(messages),
            "last_message_role": last_msg.get("role"),
            "last_message_tool_name": last_msg.get("tool_name"),
            "last_message_content_preview": (last_msg.get("content") or "")[:1000],
            "last_message_tool_calls": last_msg.get("tool_calls"),
        })
        return {
            "answer": None,
            "trace": trace,
            "tool_call_counts": session._call_counts_snapshot(),
            "status": status,
            "n_rejected": n_rejected,
            "rejected": rejected_all,
            "error": err,
        }

    return {
        "answer": answer,
        "trace": trace,
        "tool_call_counts": session._call_counts_snapshot(),
        "status": status,
        "n_rejected": n_rejected,
        "rejected": rejected_all,
        "error": None,
    }
