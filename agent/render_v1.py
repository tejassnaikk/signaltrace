"""Rendering functions for the M6 agent (v1).

Exact match for git 2ff263c:agent/render.py.  Kept separately so the version
switch can route to v1's flat-list format without touching v4's rendering.

v1 differences from v4:
- _VERDICT_TO_CLAIM_AS maps "arithmetic" to "exact" (v4 uses "mix")
- render_test_dimension returns a flat list with a claim_as field on each row
- render_ledger_summary returns entry_id + verdict (v4 returns short_id + evidence)
"""
from __future__ import annotations

_IMMATERIAL = "immaterial"

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
