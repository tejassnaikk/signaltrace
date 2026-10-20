"""Version registry for the M6 agent (PLAN.md Section 4.9).

Each VersionCfg packages everything that varies between prompt versions:
system prompt, tool schemas, render functions, and strength mapping.
Selecting a version replaces ALL of these atomically.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from metrics.dependency_graph import DIMENSIONS, REVENUE_FACTORS
from agent.prompt import SYSTEM_PROMPT as _V4_SYSTEM_PROMPT
import agent.prompt_v1 as _pv1
import agent.render as _rv4
import agent.render_v1 as _rv1

_ALLOWED_METRICS = list(REVENUE_FACTORS)
_ALLOWED_DIMENSIONS = list(DIMENSIONS)
_PATTERNS = ("localized", "broad", "mix", "none")
_DIRECTIONS = ("up", "down")


@dataclass
class VersionCfg:
    name: str
    system_prompt: str
    prompt_version: str
    valid_strengths: tuple[str, ...]
    strength_to_verdict: dict[str, str]
    verdict_to_claim_as: Callable[[str], str]
    render_test_dimension: Callable
    render_ledger_summary: Callable
    tools: list[dict]


def _make_tools(
    strengths: tuple,
    td_description: str,
    ls_description: str,
    entry_id_description: str,
    strength_description: str,
) -> list[dict]:
    return [
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
                "description": td_description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {"type": "string", "enum": _ALLOWED_METRICS},
                        "dimension": {"type": "string", "enum": _ALLOWED_DIMENSIONS},
                    },
                    "required": ["metric", "dimension"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_ledger_summary",
                "description": ls_description,
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
                                        "description": entry_id_description,
                                    },
                                    "direction": {
                                        "type": "string",
                                        "enum": list(_DIRECTIONS),
                                        "description": "Whether this entry moved up or down.",
                                    },
                                    "strength": {
                                        "type": "string",
                                        "enum": list(strengths),
                                        "description": strength_description,
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


_V1_STRENGTHS = ("strong", "plausible", "untested", "exact")
_V1_STRENGTH_TO_VERDICT: dict[str, str] = {
    v: k for k, v in _rv1._VERDICT_TO_CLAIM_AS.items()
}
_V1_TOOLS = _make_tools(
    strengths=_V1_STRENGTHS,
    td_description=(
        "Test every segment of one dimension for one metric. Returns "
        "segment, kind, direction, verdict, contribution, q_value, and "
        "entry_id for each non-immaterial row. Immaterial rows "
        "(contribution < 5%) are collapsed to a count note."
    ),
    ls_description=(
        "Compact list of non-immaterial evidence entries recorded so far: "
        "entry_id and verdict only. Use to review coverage and find "
        "entry_ids for claims."
    ),
    entry_id_description="Ledger entry_id from test_dimension output.",
    strength_description=(
        "strong/plausible for significance-tested rows; "
        "untested for aov segments or ineligible rows; "
        "exact for arithmetic mix rows."
    ),
)

_V4_STRENGTHS = ("strong", "plausible", "untested", "mix")
_V4_STRENGTH_TO_VERDICT: dict[str, str] = {
    v: k for k, v in _rv4._VERDICT_TO_CLAIM_AS.items()
}
_V4_TOOLS = _make_tools(
    strengths=_V4_STRENGTHS,
    td_description=(
        "Test every segment of one dimension for one metric. Returns "
        "two blocks: Citable rows (short_id, segment, kind, direction, "
        "contribution, evidence, basis, eligible, q) and Context rows "
        "(segment, kind, direction, contribution, verdict, basis, "
        "eligible, q). Immaterial rows collapsed to a note in Context."
    ),
    ls_description=(
        "Compact list of citable evidence entries recorded so far: "
        "short_id and evidence only. Use to review coverage and find "
        "short_ids for claims."
    ),
    entry_id_description="short_id from a Citable row in test_dimension output.",
    strength_description=(
        "Copy the evidence word from the Citable row "
        "(strong / plausible / untested / mix)."
    ),
)

V1_CFG = VersionCfg(
    name="v1",
    system_prompt=_pv1.SYSTEM_PROMPT,
    prompt_version=_pv1.VERSION,
    valid_strengths=_V1_STRENGTHS,
    strength_to_verdict=_V1_STRENGTH_TO_VERDICT,
    verdict_to_claim_as=_rv1.verdict_to_claim_as,
    render_test_dimension=_rv1.render_test_dimension,
    render_ledger_summary=_rv1.render_ledger_summary,
    tools=_V1_TOOLS,
)

V4_CFG = VersionCfg(
    name="v4",
    system_prompt=_V4_SYSTEM_PROMPT,
    prompt_version="v4",
    valid_strengths=_V4_STRENGTHS,
    strength_to_verdict=_V4_STRENGTH_TO_VERDICT,
    verdict_to_claim_as=_rv4.verdict_to_claim_as,
    render_test_dimension=_rv4.render_test_dimension,
    render_ledger_summary=_rv4.render_ledger_summary,
    tools=_V4_TOOLS,
)

_REGISTRY: dict[str, VersionCfg] = {"v1": V1_CFG, "v4": V4_CFG}


def get_version(name: str) -> VersionCfg:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown version {name!r}; available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]
