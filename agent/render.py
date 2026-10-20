"""Rendering functions for M6 agent tool outputs (data/rendering separation).

The session (agent/tools.py) handles data: running the engine, managing the
harness ledger, generating entry IDs.  These functions handle rendering:
deciding which fields the model sees and how they are formatted.

Token-budget rules encoded here:
- immaterial rows (verdict == "immaterial") are collapsed to a count note
  in the Context block.  They are never claimable.
- test_dimension returns two blocks:
    citable: rows the model can claim (short_id + evidence, no verdict)
    context: non-claimable rows + immaterial note (no short_id)
- get_ledger_summary lists only citable entries: short_id + evidence.
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
    "arithmetic": "mix",
}


def verdict_to_claim_as(verdict: str) -> str:
    """Return the strength word to use when claiming this row, or '-' if not claimable."""
    return _VERDICT_TO_CLAIM_AS.get(verdict, "-")


def render_test_dimension(rows: list[dict]) -> dict:
    """Split rows into citable and context blocks.

    Citable rows: claimable (verdict maps to a strength word).
    Columns: short_id | segment | kind | direction | contribution | evidence |
             basis | eligible | q
    The evidence column holds the claim_as value (strong/plausible/untested/exact).
    No verdict column; short_id equals the entry_id value.

    Context rows: non-claimable material rows plus the immaterial count note.
    Columns: segment | kind | direction | contribution | verdict | basis |
             eligible | q
    No short_id — these rows are invisible to the claim checker.
    """
    material = [r for r in rows if r.get("verdict") != _IMMATERIAL]
    n_skip = len(rows) - len(material)

    citable: list[dict] = []
    context: list[dict] = []

    for r in material:
        ca = verdict_to_claim_as(r.get("verdict", ""))
        if ca != "-":
            citable.append({
                "short_id": r.get("entry_id"),
                "segment": r.get("segment"),
                "kind": r.get("kind"),
                "direction": r.get("direction"),
                "contribution": r.get("contribution"),
                "evidence": ca,
                "basis": r.get("basis"),
                "eligible": r.get("eligible"),
                "q": r.get("q_value"),
            })
        else:
            context.append({
                "segment": r.get("segment"),
                "kind": r.get("kind"),
                "direction": r.get("direction"),
                "contribution": r.get("contribution"),
                "verdict": r.get("verdict"),
                "basis": r.get("basis"),
                "eligible": r.get("eligible"),
                "q": r.get("q_value"),
            })

    # Segment rows first (existing order preserved within each group), mix row last.
    citable.sort(key=lambda r: 0 if r["kind"] == "segment" else 1)

    if n_skip:
        context.append({"note": f"{n_skip} immaterial row(s) omitted (contribution < 5%)"})

    return {"citable": citable, "context": context}


def render_ledger_summary(entries: list) -> list[dict]:
    """Compact ledger: citable entries only, showing short_id and evidence.

    Immaterial entries excluded (never claimable).
    Non-claimable material entries excluded (context-only, no short_id shown).
    """
    out = []
    for e in entries:
        if e.verdict == _IMMATERIAL:
            continue
        ca = verdict_to_claim_as(e.verdict)
        if ca == "-":
            continue
        out.append({"short_id": e.evidence_id, "evidence": ca})
    return out
