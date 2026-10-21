"""Synthetic incident injection for the M3 benchmark (PLAN.md Section 5).

Builds an in-memory copy of fact_sessions / metrics_daily / order_concentration
with one incident's mechanism applied to a single date, then hands back a
DuckDB connection to that copy. The on-disk DB is opened read-only and never
written to. Nothing in detection/, investigation/, or metrics/ changes to
consume the result - every read path there already accepts an optional `con`.

Mechanism kinds: "scale_segment" (I1/I2/I3/I3b/I5), "move_sessions" (I6),
"compound" (I7, a list of sub-mechanisms applied in sequence),
"scale_uniform" (I4, a proportional cut applied to every row), "none" (C1,
the negative control - returns the real data unmodified).
"""
from __future__ import annotations

import json
import os
import sys

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "signaltrace.duckdb")
GROUND_TRUTH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "ground_truth")


def load_ground_truth(incident_id: str) -> dict:
    with open(os.path.join(GROUND_TRUTH_DIR, f"{incident_id}.json")) as f:
        return json.load(f)


def _largest_remainder_round(values: pd.Series) -> pd.Series:
    """Round a Series of non-negative floats to integers so the SUM of the
    rounded values equals round(sum(values)) exactly, rather than each row
    independently rounding to nearest (which systematically rounds toward
    zero whenever `values` are individually small relative to 1 - e.g.
    session counts x a ~1% conversion rate, where almost every per-row
    product is < 0.5 and plain rounding would lose most of the total)."""
    floors = np.floor(values)
    total_target = int(round(values.sum()))
    deficit = total_target - int(floors.sum())
    if deficit <= 0:
        return floors.astype("int64")
    remainders = (values - floors).sort_values(ascending=False)
    bump_idx = remainders.index[:deficit]
    out = floors.copy()
    out.loc[bump_idx] += 1
    return out.astype("int64")


def _scale_segment(fact: pd.DataFrame, date: pd.Timestamp, dimension: str,
                   segment: str, sessions_factor: float,
                   transactions_factor: float,
                   revenue_factor: float) -> pd.DataFrame:
    """Scale sessions/transactions/revenue for one segment on one date only.
    Every other row - every other segment, every other date - is untouched
    (PLAN.md 5.1, "Untouched-segment convention")."""
    out = fact.copy()
    mask = (out["date"] == date) & (out[dimension] == segment)
    if not mask.any():
        raise ValueError(f"no fact_sessions rows for {dimension}={segment!r} "
                          f"on {date.date()}")
    # Largest-remainder rounding (see _largest_remainder_round): plain
    # per-row round() systematically loses total when the scaled values are
    # small relative to 1 (e.g. a segment with only a handful of
    # transactions spread across many channel/device/country rows).
    out.loc[mask, "sessions"] = _largest_remainder_round(out.loc[mask, "sessions"] * sessions_factor)
    out.loc[mask, "transactions"] = _largest_remainder_round(out.loc[mask, "transactions"] * transactions_factor)
    out.loc[mask, "revenue"] = out.loc[mask, "revenue"] * revenue_factor
    out["sessions"] = out["sessions"].astype("int64")
    out["transactions"] = out["transactions"].astype("int64")
    return out


def _move_sessions(fact: pd.DataFrame, date: pd.Timestamp, dimension: str,
                   from_segment: str, to_segment: str, fraction: float
                   ) -> pd.DataFrame:
    """Reallocate `fraction` of from_segment's sessions to to_segment on one
    date, at every (other-dimension) cell those segments appear in. Each
    segment's own conversion_rate/AOV is held at its real (whole-segment,
    that date) value: from_segment loses sessions/transactions/revenue in
    the SAME proportion in every row (preserves its own rate exactly, per-
    row and in aggregate - same trick as _scale_segment); to_segment gains
    sessions at its OWN real rate (computed once, whole-segment, for the
    date), so its rate is unaffected by the inflow either. Total sessions
    unchanged - this is a pure mix shift (PLAN.md 5.1, I6): neither
    segment's own rate moves, only the weights.
    """
    day = fact["date"] == date
    from_mask = day & (fact[dimension] == from_segment)
    to_mask = day & (fact[dimension] == to_segment)
    if not from_mask.any():
        raise ValueError(f"no fact_sessions rows for {dimension}={from_segment!r} "
                          f"on {date.date()}")
    other_dims = [d for d in ("channel", "device", "country") if d != dimension]

    to_totals = fact.loc[to_mask, ["sessions", "transactions", "revenue"]].sum()
    to_cr = (to_totals["transactions"] / to_totals["sessions"]
            if to_totals["sessions"] > 0 else 0.0)
    to_aov = (to_totals["revenue"] / to_totals["transactions"]
             if to_totals["transactions"] > 0 else 0.0)

    # Largest-remainder rounding, not per-row round(): to_cr is often small
    # (e.g. mobile's ~1.2% conversion rate), so round(moved_sessions * to_cr)
    # per row rounds almost every individual cell down to 0 and loses most
    # of the total - a systematic downward bias, not neutral noise, because
    # the values being rounded are small relative to 1. Rounding once at the
    # aggregate and distributing by remainder keeps to_segment's own rate
    # correct in total, which is the property this mechanism promises.
    moved = fact.loc[from_mask, other_dims + ["sessions"]].copy()
    moved["moved_sessions"] = _largest_remainder_round(moved["sessions"] * fraction)
    moved["moved_transactions"] = _largest_remainder_round(moved["moved_sessions"] * to_cr)
    moved["moved_revenue"] = moved["moved_transactions"] * to_aov
    moved = moved[other_dims + ["moved_sessions", "moved_transactions", "moved_revenue"]]
    moved = moved[moved["moved_sessions"] > 0]

    from_rows = fact.loc[from_mask].copy()
    from_rows["sessions"] = _largest_remainder_round(from_rows["sessions"] * (1 - fraction))
    from_rows["transactions"] = _largest_remainder_round(from_rows["transactions"] * (1 - fraction))
    from_rows["revenue"] = from_rows["revenue"] * (1 - fraction)

    to_rows = fact.loc[to_mask].copy()
    to_rows = to_rows.merge(moved, on=other_dims, how="outer")
    to_rows["date"] = date
    to_rows[dimension] = to_segment
    for col in ("sessions", "transactions"):
        to_rows[col] = (to_rows[col].fillna(0) + to_rows[f"moved_{col}"].fillna(0)).round().astype("int64")
    to_rows["revenue"] = to_rows["revenue"].fillna(0.0) + to_rows["moved_revenue"].fillna(0.0)
    to_rows = to_rows.drop(columns=["moved_sessions", "moved_transactions", "moved_revenue"])

    rest = fact.loc[day & ~fact[dimension].isin([from_segment, to_segment])]
    other_dates = fact.loc[~day]
    cols = fact.columns.tolist()
    return pd.concat([other_dates, rest, from_rows, to_rows], ignore_index=True)[cols]


def _scale_uniform(fact: pd.DataFrame, date: pd.Timestamp,
                   revenue_factor: float) -> pd.DataFrame:
    """Scale revenue by `revenue_factor` for EVERY row on one date - a
    proportional cut applied uniformly across every channel/device/country
    cell, not one segment (PLAN.md 5.1, I4). Sessions and transactions held
    everywhere. No rounding needed: revenue is a continuous float, so this is
    exact, unlike the segment-level mechanisms which round integer counts."""
    out = fact.copy()
    mask = out["date"] == date
    if not mask.any():
        raise ValueError(f"no fact_sessions rows on {date.date()}")
    out.loc[mask, "revenue"] = out.loc[mask, "revenue"] * revenue_factor
    return out


def _apply_mechanism(fact: pd.DataFrame, date: pd.Timestamp, mech: dict) -> pd.DataFrame:
    if mech["kind"] == "none":
        return fact
    if mech["kind"] == "scale_uniform":
        return _scale_uniform(fact, date, mech["revenue_factor"])
    if mech["kind"] == "scale_segment":
        return _scale_segment(
            fact, date, mech["dimension"], mech["segment"],
            mech["sessions_factor"], mech["transactions_factor"],
            mech["revenue_factor"])
    if mech["kind"] == "move_sessions":
        return _move_sessions(
            fact, date, mech["dimension"], mech["from_segment"],
            mech["to_segment"], mech["fraction"])
    if mech["kind"] == "compound":
        for sub in mech["mechanisms"]:
            fact = _apply_mechanism(fact, date, sub)
        return fact
    raise NotImplementedError(
        f"injector mechanism {mech['kind']!r} not yet built")


def _build_con_impl(incident_id: str, date: pd.Timestamp) -> duckdb.DuckDBPyConnection:
    """Shared implementation for build_incident_con and build_incident_con_any_date.
    Caller is responsible for date validation."""
    gt = load_ground_truth(incident_id)
    real = duckdb.connect(DB, read_only=True)
    fact = real.execute("SELECT * FROM fact_sessions").df()
    fact["date"] = pd.to_datetime(fact["date"])
    conc = real.execute("SELECT * FROM order_concentration").df()
    conc["date"] = pd.to_datetime(conc["date"])
    real.close()

    fact = _apply_mechanism(fact, date, gt["mechanism"])
    daily = fact.groupby("date", as_index=False).agg(
        sessions=("sessions", "sum"),
        transactions=("transactions", "sum"),
        revenue=("revenue", "sum"))
    daily["conversion_rate"] = daily["transactions"] / daily["sessions"].where(daily["sessions"] > 0)
    daily["aov"] = daily["revenue"] / daily["transactions"].where(daily["transactions"] > 0)

    con = duckdb.connect(":memory:")
    con.register("_fact", fact)
    con.execute("CREATE TABLE fact_sessions AS SELECT * FROM _fact")
    con.register("_daily", daily)
    con.execute("CREATE TABLE metrics_daily AS SELECT * FROM _daily")
    con.register("_conc", conc)
    con.execute("CREATE TABLE order_concentration AS SELECT * FROM _conc")
    con.unregister("_fact")
    con.unregister("_daily")
    con.unregister("_conc")
    return con


def build_incident_con(incident_id: str, date: str | pd.Timestamp
                       ) -> duckdb.DuckDBPyConnection:
    """Returns a fresh in-memory connection whose fact_sessions/metrics_daily/
    order_concentration reflect `incident_id`'s ground-truth mechanism applied
    to `date`. Pass this as investigate(..., con=this) - never mutates
    data/signaltrace.duckdb."""
    gt = load_ground_truth(incident_id)
    date = pd.Timestamp(date)
    if date not in pd.to_datetime(gt["dates"]).tolist():
        raise ValueError(f"{date.date()} is not one of {incident_id}'s "
                         f"pre-registered dates: {gt['dates']}")
    return _build_con_impl(incident_id, date)


def build_incident_con_any_date(incident_id: str, date: str | pd.Timestamp
                                ) -> duckdb.DuckDBPyConnection:
    """Same as build_incident_con but without the dev-date registration check.

    Use ONLY for the held-out split (evaluation/run_agent.py --split heldout
    and evaluation/run_heldout_engine.py).  The dev-date check in
    build_incident_con is a safety rail against accidental held-out leakage;
    this function exists solely so both held-out callers use identical
    injection logic.  Do not call this from dev-mode code paths.
    """
    return _build_con_impl(incident_id, pd.Timestamp(date))


if __name__ == "__main__":
    con = build_incident_con("I1", "2016-10-24")
    check = con.execute("""
        SELECT channel, SUM(sessions) sessions, SUM(transactions) txns,
               SUM(revenue) revenue
        FROM fact_sessions WHERE date = '2016-10-24' GROUP BY channel
        ORDER BY sessions DESC
    """).df()
    print(check.to_string(index=False))
    print()
    print(con.execute(
        "SELECT * FROM metrics_daily WHERE date = '2016-10-24'").df())
    con.close()
