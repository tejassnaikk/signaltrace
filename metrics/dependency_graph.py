"""Metric dependency graph. DECLARED, not learned.

Learning the graph from data is a separate research project and would consume
the whole build. Declaring it is also more honest: these relationships are
arithmetic identities, not statistical discoveries.

    Revenue = Sessions x ConversionRate x AOV     (exact, verified 0.0 error)

Sessions decomposes ADDITIVELY across each dimension:
    Sessions = sum over channels = sum over devices = sum over countries

That distinction drives attribution: multiplicative factors need a
decomposition formula, additive segments need a share calculation.
"""
from __future__ import annotations

REVENUE_FACTORS = ["sessions", "conversion_rate", "aov"]
DIMENSIONS = ["channel", "device", "country"]

GRAPH = {
    "revenue": {"type": "derived", "relation": "multiplicative",
                "factors": REVENUE_FACTORS,
                "formula": "sessions * conversion_rate * aov"},
    "sessions": {"type": "observed", "relation": "additive",
                 "dimensions": DIMENSIONS},
    "conversion_rate": {"type": "derived", "relation": "ratio",
                        "numerator": "transactions", "denominator": "sessions",
                        "dimensions": DIMENSIONS},
    "aov": {"type": "derived", "relation": "ratio",
            "numerator": "revenue", "denominator": "transactions",
            "dimensions": DIMENSIONS},
    "transactions": {"type": "observed", "relation": "additive",
                     "dimensions": DIMENSIONS},
}


def factors_of(metric: str) -> list[str]:
    return GRAPH.get(metric, {}).get("factors", [])


def dimensions_of(metric: str) -> list[str]:
    return GRAPH.get(metric, {}).get("dimensions", [])


def is_ratio(metric: str) -> bool:
    return GRAPH.get(metric, {}).get("relation") == "ratio"


def traversal_order(root: str = "revenue") -> list[tuple[str, int]]:
    """Breadth-first walk from root: (metric, depth) pairs.

    This is the order a deterministic investigation follows. The Phase 2 agent
    may choose a different order - comparing the two IS the experiment.
    """
    out, queue, seen = [], [(root, 0)], set()
    while queue:
        m, d = queue.pop(0)
        if m in seen:
            continue
        seen.add(m)
        out.append((m, d))
        for f in factors_of(m):
            queue.append((f, d + 1))
    return out
