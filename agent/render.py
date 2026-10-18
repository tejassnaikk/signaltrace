"""Rendering functions for M6 agent tool outputs (data/rendering separation).

The session (agent/tools.py) handles data: running the engine, managing the
harness ledger, generating entry IDs.  These functions handle rendering:
deciding which fields the model sees and how they are formatted.

Token-budget rules encoded here:
- immaterial rows (verdict == "immaterial") are collapsed to a count.
  They are never claimable, so excluding them does not lose information.
- get_ledger_summary is compact: only entry_id, identifiers, verdict,
  direction, and contribution.  actual/expected/p_value/etc. are not needed.
"""
from __future__ import annotations

_IMMATERIAL = "immaterial"

# Shared verdict → claim strength mapping.  This is the single source of truth
# used by both render_test_dimension (what the model sees) and loop._check_claims
# (what the checker accepts), so they cannot drift apart.
_VERDICT_TO_CLAIM_AS: dict[str, str] = {
    "strong": "strong",
    "plausible": "plausible",
    "material_untested": "untested",
    "arithmetic": "exact",
}


def verdict_to_claim_as(verdict: str) -> str:
    """Return the strength word to use when claiming this row, or '-' if not claimable."""
    return _VERDICT_TO_CLAIM_AS.get(verdict, "-")


def render_test_dimension(rows: list[dict]) -> list:
    """Drop immaterial rows (replace with a count note), keep all others.

    Adds a claim_as field to each kept row: the strength word the agent should
    copy when claiming this row, or '-' for rows that cannot be claimed.
    """
    material = [r for r in rows if r.get("verdict") != _IMMATERIAL]
    n_skip = len(rows) - len(material)
    result = [{**r, "claim_as": verdict_to_claim_as(r.get("verdict", ""))} for r in material]
    if n_skip:
        result.append({"note": f"{n_skip} immaterial row(s) omitted (contribution < 5%)"})
    return result


def render_ledger_summary(entries: list) -> list[dict]:
    """Minimal ledger: entry_id + verdict only.

    The agent already saw segment/metric/dimension/contribution in the
    test_dimension results; the ledger is only a citation cross-reference.
    Immaterial entries excluded (never claimable).
    """
    out = []
    for e in entries:
        if e.verdict == _IMMATERIAL:
            continue
        out.append({"entry_id": e.evidence_id, "verdict": e.verdict})
    return out
