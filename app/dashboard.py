"""SignalTrace dashboard (M4). Presentation layer only - every number and
category on screen comes from investigate()'s own return value or from an
existing evaluation/score.py function. No statistics computed here.

Two tabs:
    1. Investigate a date - runs investigate() against real, unmodified data
       for a chosen date.
    2. Benchmark incidents - runs the injector + forced investigate() for one
       of the 8 pre-registered M3 incidents, scored against its own ground
       truth via the existing SCORERS functions.

Run with: streamlit run app/dashboard.py
"""
from __future__ import annotations

import os
import sys

import duckdb
import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from detection.config import DETECTION_END, DETECTION_START
from evaluation.injector import load_ground_truth
from evaluation.score import SCORERS, _run
from investigation.orchestrator import DB, investigate
from metrics.dependency_graph import DIMENSIONS, GRAPH, REVENUE_FACTORS

st.set_page_config(page_title="SignalTrace", layout="wide")

# Colors map directly onto the engine's own categorical fields - no new
# numeric thresholds are chosen here, only labels for values investigate()
# already produced.
VERDICT_COLOR = {
    "strong": "#1a7f37",
    "plausible": "#2f6fed",
    "material_but_uncertain": "#b98900",
    "material_untested": "#6e7781",
    "unsupported": "#8c8c8c",
    "immaterial": "#c9ccd1",
    "arithmetic": "#6b46c1",
    "explained_single_order": "#8250df",
}
STATUS_COLOR = {"fired": "#1a7f37", "suppressed": "#8250df", "quiet": "#8c8c8c"}
STATUS_LABEL = {"fired": "FIRED", "suppressed": "SUPPRESSED", "quiet": "no fire"}


@st.cache_resource
def _real_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB, read_only=True)


def _component_status(r: dict) -> dict[str, str]:
    """One classification, read once from investigate()'s own fired/
    suppressed/today frames - never re-derived from is_anomaly/
    single_order_driven directly. Both the status strip and the dependency
    graph annotation call this same function and get the same dict, so
    there is exactly one reading of "what happened to this component today",
    not two independently-computed ones."""
    today = r["today"]
    fired_metrics = set(r["fired"]["metric"]) if not r["fired"].empty else set()
    suppressed_metrics = set(r["suppressed"]["metric"]) if not r["suppressed"].empty else set()
    status = {}
    for m in today["metric"]:
        if m in fired_metrics:
            status[m] = "fired"
        elif m in suppressed_metrics:
            status[m] = "suppressed"
        else:
            status[m] = "quiet"
    return status


def _fmt_share(c) -> str:
    """Same convention as investigation/orchestrator.py's _fmt_share: a
    contribution is a share OF the movement, not a signed change - negative
    means the segment moved AGAINST the metric's own direction."""
    if c is None or pd.isna(c):
        return "n/a"
    return f"{abs(c):.1%} opp" if c < 0 else f"{c:.1%}"


def _fmt_abs(v, metric: str) -> str:
    if v is None or pd.isna(v):
        return ""
    if metric in ("sessions", "transactions"):
        return f"{v:+,.0f}"
    if metric == "conversion_rate":
        return f"{v:+.4f}pp"
    return f"{v:+,.2f}"


def render_status_strip(status: dict[str, str]) -> None:
    st.subheader("Component status")
    cols = st.columns(len(REVENUE_FACTORS))
    for col, metric in zip(cols, REVENUE_FACTORS):
        s = status.get(metric, "quiet")
        col.markdown(
            f"**{metric}**  \n"
            f"<span style='color:{STATUS_COLOR[s]};font-weight:600'>{STATUS_LABEL[s]}</span>",
            unsafe_allow_html=True,
        )


def render_dependency_graph(status: dict[str, str]) -> None:
    st.subheader("Dependency graph traversal")
    st.caption(
        "Declared, not learned (metrics/dependency_graph.py). Node status is "
        "this date's fired/suppressed/quiet classification - the same values "
        "as the strip above, not a separate reading of it."
    )
    st.markdown("**revenue** = sessions x conversion_rate x aov")
    cols = st.columns(len(REVENUE_FACTORS))
    for col, metric in zip(cols, REVENUE_FACTORS):
        s = status.get(metric, "quiet")
        dims = ", ".join(GRAPH[metric].get("dimensions", []))
        col.markdown(
            f"<div style='border:1px solid #ddd;border-radius:6px;padding:8px;"
            f"text-align:center'>"
            f"<span style='color:{STATUS_COLOR[s]};font-weight:600'>{metric}</span><br>"
            f"<span style='font-size:0.8em;color:#666'>{STATUS_LABEL[s]}</span><br>"
            f"<span style='font-size:0.75em;color:#888'>by {dims}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


def render_factor_table(factors: pd.DataFrame | None) -> None:
    st.subheader("Revenue factor decomposition")
    if factors is None:
        st.caption("Not available - one or more factors lacked a valid expectation on this date.")
        return
    disp = factors.copy()
    disp["share of move"] = disp["contribution"].apply(_fmt_share)
    disp["pct_change"] = disp["pct_change"].apply(lambda x: f"{x:+.1%}" if pd.notna(x) else "")
    st.dataframe(disp[["actual", "expected", "pct_change", "share of move"]],
                width="stretch")


def render_segment_results(ledger_df: pd.DataFrame) -> None:
    st.subheader("Ranked segment results")
    if ledger_df.empty or "kind" not in ledger_df.columns:
        st.caption("No component fired on this date - nothing was segment-tested.")
        return
    seg = ledger_df[ledger_df["kind"].isin(["segment", "mix"])]
    if seg.empty:
        st.caption("No component fired on this date - nothing was segment-tested.")
        return
    for metric in sorted(seg["metric"].unique()):
        st.markdown(f"**{metric}**")
        m_rows = seg[seg["metric"] == metric]
        tabs = st.tabs(DIMENSIONS)
        for tab, dim in zip(tabs, DIMENSIONS):
            with tab:
                d_rows = m_rows[m_rows["dimension"] == dim].copy()
                if d_rows.empty:
                    st.caption("no rows")
                    continue
                d_rows["segment"] = d_rows["segment"].fillna("(traffic mix)")
                d_rows["share of move"] = d_rows["contribution"].apply(_fmt_share)
                d_rows["magnitude"] = d_rows.apply(
                    lambda row: _fmt_abs(row["abs_contribution"], metric), axis=1)
                d_rows = d_rows.reindex(
                    d_rows["contribution"].abs().sort_values(ascending=False).index)
                cols = ["segment", "share of move", "magnitude", "verdict", "basis",
                       "q_value", "detail"]
                styled = d_rows[cols].style.map(
                    lambda v: f"background-color:{VERDICT_COLOR.get(v, '#ffffff')}22",
                    subset=["verdict"])
                st.dataframe(styled, width="stretch")


def render_ledger(ledger) -> None:
    # Boxed and left-accented on purpose, not just another st.subheader - this
    # is "the differentiator" (negative results retained, tested-vs-
    # decomposition-only distinguished), so it needs to read as its own block
    # at a glance, not as one more table in the same visual rhythm as the
    # ranked-segment tables above it.
    st.markdown(
        "<div style='border-left:4px solid #6b46c1;padding-left:12px'>"
        "<h3 style='margin-bottom:0'>Evidence ledger</h3>"
        "<span style='color:#6b46c1;font-size:0.85em'>every test that ran, including the ones that found nothing</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    with st.container(border=True):
        df = ledger.to_frame()
        if df.empty:
            st.info("No entries - nothing was investigated on this date.")
            return
        st.caption(ledger.summary())
        n_supported, n_negative = len(ledger.supported()), len(ledger.negative())
        n_other = len(df) - n_supported - n_negative
        st.caption(
            f"{n_supported} supported, {n_negative} negative (kept, not discarded), "
            f"{n_other} other (concentration / single-order findings)"
        )
        view = st.radio("View", ["All", "Supported", "Negative"], horizontal=True,
                        key=f"ledger_view_{ledger.incident_id}_{ledger.date}")
        if view == "Supported":
            show = pd.DataFrame([vars(e) for e in ledger.supported()])
        elif view == "Negative":
            show = pd.DataFrame([vars(e) for e in ledger.negative()])
        else:
            show = df
        cols = ["evidence_id", "metric", "kind", "dimension", "segment", "contribution",
               "verdict", "basis", "q_value", "detail"]
        if show.empty:
            st.caption(f"No entries in this view ({view}).")
            return
        styled = show[cols].style.map(
            lambda v: f"background-color:{VERDICT_COLOR.get(v, '#ffffff')}22",
            subset=["verdict"])
        st.dataframe(styled, width="stretch")


def render_investigation(r: dict) -> None:
    status = _component_status(r)
    render_status_strip(status)
    render_dependency_graph(status)
    render_factor_table(r["factors"])
    render_segment_results(r["ledger"].to_frame())
    render_ledger(r["ledger"])


st.title("SignalTrace")

tab1, tab2 = st.tabs(["Investigate a date", "Benchmark incidents"])

with tab1:
    st.caption("Runs investigate() against real, unmodified data for a chosen date.")
    min_d, max_d = pd.Timestamp(DETECTION_START).date(), pd.Timestamp(DETECTION_END).date()
    date = st.date_input("Date", value=min_d, min_value=min_d, max_value=max_d)
    if st.button("Run investigation", key="run_date"):
        r = investigate(pd.Timestamp(date), con=_real_con(), verbose=False)
        render_investigation(r)

with tab2:
    st.caption(
        "Runs the injector plus a forced investigate() call for one of the 8 "
        "pre-registered M3 benchmark incidents, scored against its own "
        "ground-truth pass condition (evaluation/ground_truth/)."
    )
    incident_id = st.selectbox(
        "Incident", ["I1", "I2", "I3", "I3b", "I4", "I5", "I6", "I7", "C1"])
    gt = load_ground_truth(incident_id)
    st.markdown(f"**{gt['description']}**")
    with st.expander("Mechanism and pass condition (ground truth)"):
        st.json({"mechanism": gt["mechanism"], "pass_condition": gt["pass_condition"]})
    date_str = st.selectbox("Pre-registered date", gt["dates"])
    if st.button("Run benchmark incident", key="run_incident"):
        r = _run(incident_id, date_str, gt["forced_metrics"])
        dr = SCORERS[incident_id](gt, date_str)
        if incident_id == "C1":
            m = dr.measurements
            st.info(
                "Measurement, not pass/fail (PLAN.md Section 5.3) - C1 is a "
                "negative control with no injection; this reports how often "
                "ordinary real noise reaches a `strong` verdict."
            )
            if m["n_material_tested"]:
                rate = m["n_strong"] / m["n_material_tested"]
                st.metric("False-strong rate (material-tested pairs)",
                         f"{m['n_strong']}/{m['n_material_tested']} ({rate:.1%})")
            else:
                st.metric("False-strong rate (material-tested pairs)", "n/a")
        else:
            st.markdown("### " + ("PASS" if dr.passed else "FAIL"))
        with st.expander("Measured detail"):
            st.json(dr.measurements if isinstance(dr.measurements, dict) else {})
        render_investigation(r)
