"""Characterize the real series before any detection logic exists."""
import os
import duckdb
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
con = duckdb.connect(os.path.join(ROOT, "data", "signaltrace.duckdb"))
df = con.execute("SELECT * FROM metrics_daily ORDER BY date").df()
df["date"] = pd.to_datetime(df["date"])
df["dow"] = df["date"].dt.day_name()

print("=" * 62)
print("SCALE AND DISPERSION")
print("=" * 62)
for m in ["sessions", "transactions", "revenue", "conversion_rate", "aov"]:
    s = df[m].dropna()
    print(f"{m:16s} mean={s.mean():10.3f}  med={s.median():10.3f}  "
          f"cv={s.std()/s.mean():5.2f}  min={s.min():9.2f}  max={s.max():10.2f}")

print()
print("=" * 62)
print("WEEKLY SEASONALITY  (revenue by weekday, index vs overall mean)")
print("=" * 62)
order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
g = df.groupby("dow")["revenue"].agg(["mean", "std"]).reindex(order)
overall = df["revenue"].mean()
for d, r in g.iterrows():
    print(f"{d:10s} mean={r['mean']:9.0f}  index={r['mean']/overall:5.2f}  "
          f"cv={r['std']/r['mean']:5.2f}")

print()
print("=" * 62)
print("TREND  (monthly revenue and sessions)")
print("=" * 62)
mo = df.set_index("date").resample("MS")[["sessions","revenue"]].sum()
for d, r in mo.iterrows():
    print(f"{d:%Y-%m}  sessions={r['sessions']:9.0f}  revenue={r['revenue']:11.0f}")

print()
print("=" * 62)
print("EXTREMES  (top 8 and bottom 5 revenue days)")
print("=" * 62)
for _, r in df.nlargest(8, "revenue").iterrows():
    print(f"HIGH {r['date']:%Y-%m-%d} {r['dow']:9s} rev={r['revenue']:10.0f} "
          f"sess={r['sessions']:6.0f} txn={r['transactions']:4.0f} aov={r['aov']:8.2f}")
for _, r in df.nsmallest(5, "revenue").iterrows():
    print(f"LOW  {r['date']:%Y-%m-%d} {r['dow']:9s} rev={r['revenue']:10.0f} "
          f"sess={r['sessions']:6.0f} txn={r['transactions']:4.0f}")

print()
print("=" * 62)
print("RESIDUAL SPREAD vs SEASONAL-NAIVE BASELINE (same weekday, prior 4 wks)")
print("=" * 62)
s = df.set_index("date")["revenue"]
base = s.shift(7).rolling(4).mean()
resid = (s - base).dropna()
pct = (resid / base.reindex(resid.index)).replace([np.inf,-np.inf], np.nan).dropna()
mad = np.median(np.abs(resid - resid.median()))
print(f"usable points        : {len(resid)}")
print(f"residual median      : {resid.median():.1f}")
print(f"residual MAD         : {mad:.1f}   (robust sigma ~ {1.4826*mad:.1f})")
print(f"pct dev  p05/p50/p95 : {pct.quantile(.05):+.1%} / "
      f"{pct.quantile(.50):+.1%} / {pct.quantile(.95):+.1%}")
print(f"|robust z| > 3       : {(np.abs(resid - resid.median())/(1.4826*mad) > 3).sum()} days")
print(f"|robust z| > 4       : {(np.abs(resid - resid.median())/(1.4826*mad) > 4).sum()} days")

print()
print("=" * 62)
print("SEGMENT SPARSITY  (date x channel x device x country cells)")
print("=" * 62)
seg = con.execute("""
    SELECT channel, COUNT(*) AS cells, SUM(sessions) AS sessions,
           SUM(transactions) AS txns, ROUND(SUM(revenue),0) AS revenue
    FROM fact_sessions GROUP BY channel ORDER BY sessions DESC
""").df()
print(seg.to_string(index=False))
print()
print(con.execute("""
    SELECT device, COUNT(*) AS cells, SUM(sessions) AS sessions,
           SUM(transactions) AS txns, ROUND(SUM(revenue),0) AS revenue
    FROM fact_sessions GROUP BY device ORDER BY sessions DESC
""").df().to_string(index=False))
con.close()
