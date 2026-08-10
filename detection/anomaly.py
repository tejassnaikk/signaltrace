"""Component-level anomaly detection with a composed revenue verdict."""
from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import (COMPONENTS, CONCENTRATION, DETECTION_END,
                              DETECTION_START)
from detection.expectation import build_expectation


def detect_components(df: pd.DataFrame) -> pd.DataFrame:
    """Run each component's own expectation model and threshold."""
    frames = []
    for metric, cfg in COMPONENTS.items():
        e = build_expectation(df, metric, weeks=cfg["weeks"])
        e = e[~e["excluded"]].dropna(subset=["robust_z"]).copy()
        e["threshold"] = cfg["z"]
        e["is_anomaly"] = e["robust_z"].abs() > cfg["z"]
        e["direction"] = e["residual"].apply(
            lambda r: "up" if r > 0 else "down")
        frames.append(e)
    out = pd.concat(frames, ignore_index=True)
    return out[(out["date"] >= DETECTION_START) & (out["date"] <= DETECTION_END)]


def annotate_concentration(anoms: pd.DataFrame,
                           conc: pd.DataFrame) -> pd.DataFrame:
    """Flag AOV anomalies that are single-order artefacts rather than incidents.

    Not a suppression: the flag becomes an evidence-ledger entry explaining
    WHY the movement is not a business event.
    """
    c = conc.copy()
    c["max_over_median"] = c["max_order"] / c["median_order"].replace(0, pd.NA)
    m = anoms.merge(
        c[["date", "top_order_share", "max_over_median", "order_count"]],
        on="date", how="left")
    m["single_order_driven"] = (
        (m["metric"] == "aov")
        & (m["top_order_share"] > CONCENTRATION["top_order_share_min"])
        & (m["max_over_median"] > CONCENTRATION["max_over_median_min"])
    )
    return m


def compose_revenue_verdict(anoms: pd.DataFrame) -> pd.DataFrame:
    """Revenue verdict = union of component verdicts on the same date.

    Revenue never fires on its own; it inherits from whichever components moved.
    """
    fired = anoms[anoms["is_anomaly"] & ~anoms["single_order_driven"]]
    if fired.empty:
        return pd.DataFrame(columns=["date", "drivers", "n_drivers"])
    g = (fired.groupby("date")
         .apply(lambda d: ", ".join(
             f"{r.metric} {r.direction} {r.pct_dev:+.1%}"
             for r in d.itertuples()), include_groups=False)
         .reset_index(name="drivers"))
    g["n_drivers"] = fired.groupby("date").size().values
    return g


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    con = duckdb.connect(os.path.join(root, "data", "signaltrace.duckdb"))
    df = con.execute("SELECT * FROM metrics_daily ORDER BY date").df()
    conc = con.execute("SELECT * FROM order_concentration ORDER BY date").df()
    con.close()
    for f in (df, conc):
        f["date"] = pd.to_datetime(f["date"])

    a = annotate_concentration(detect_components(df), conc)
    days = a["date"].nunique()

    print(f"detection range : {DETECTION_START} to {DETECTION_END} ({days} days)")
    print(f"{'component':16s} {'fired':>6s} {'rate':>7s} {'expected':>9s}")
    print("-" * 44)
    for metric, cfg in COMPONENTS.items():
        s = a[a["metric"] == metric]
        n = int((s["is_anomaly"] & ~s["single_order_driven"]).sum())
        print(f"{metric:16s} {n:6d} {n/len(s):7.1%} {cfg['fp_floor']:9.1%}")

    sod = a[a["single_order_driven"] & a["is_anomaly"]]
    print(f"\nAOV anomalies reclassified as single-order-driven: {len(sod)}")
    if len(sod):
        print(sod[["date", "pct_dev", "top_order_share",
                   "max_over_median"]].to_string(index=False))

    r = compose_revenue_verdict(a)
    print(f"\ncomposed revenue incidents: {len(r)} days "
          f"({len(r)/days:.1%} of range)")
    print(r.head(15).to_string(index=False))
