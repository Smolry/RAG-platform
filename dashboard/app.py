"""Streamlit dashboard:  streamlit run dashboard/app.py
Reads the request_log table (DATABASE_URL). Quality shows only for requests that have a judge score (benchmarks / eval runs)."""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.observability.db import Database
from dashboard.metrics import by_route, headline, over_time

st.set_page_config(page_title="RAG Platform - Project 1", layout="wide")
st.title("RAG Platform - cost, latency, cache & routing")
db = Database(get_settings().database_url)
full = db.request_log_frame()
if full.empty:
    st.info("request_log is empty. Run queries (API) or `python -m scripts.run_benchmark` first - this page shows only logged data.")
    st.stop()

labels = sorted(full["run_label"].fillna("").unique())
pick = st.sidebar.multiselect("run_label (benchmark arm / traffic)", labels, default=labels,
                              format_func=lambda x: x or "(live traffic)")
df = full[full["run_label"].fillna("").isin(pick)]
st.sidebar.caption(f"{len(df)} of {len(full)} logged requests")

h = headline(df)
c = st.columns(5)
c[0].metric("Cost / request", f"${h['cost_per_request']:.5f}")
c[1].metric("Cache hit rate", f"{h['cache_hit_rate']:.1%}")
c[2].metric("p50 latency", f"{h['p50_ms']:.0f} ms")
c[3].metric("p95 latency", f"{h['p95_ms']:.0f} ms")
c[4].metric("Quality (judge)", "n/a" if h["quality"] is None else f"{h['quality']:.3f}", help=f"{h['n_judged']} judged requests")

l, r = st.columns(2)
with l:
    st.subheader("Route split")
    rs = pd.Series(h["route_split"], name="share")
    st.bar_chart(rs) if len(rs) else st.caption("no routed requests in selection")
    if h["escalation_rate"] is not None:
        st.caption(f"escalation rate: {h['escalation_rate']:.1%}")
with r:
    st.subheader("By route (cache hits shown as 'cache/none')")
    st.dataframe(by_route(df), hide_index=True, width="stretch")

st.subheader("Metrics over time")
freq = st.selectbox("bucket", ["min", "h", "D"], index=1)
t = over_time(df, freq)
if len(t):
    a, b = st.columns(2)
    a.line_chart(t.set_index("ts")[["cost_per_request"]])
    b.line_chart(t.set_index("ts")[["p50_ms", "p95_ms"]])
    a.line_chart(t.set_index("ts")[["cache_hit_rate"]])
    b.line_chart(t.set_index("ts")[["quality"]])
st.subheader("Per-run comparison")
st.dataframe(pd.DataFrame({k: headline(g) | {"route_split": str(headline(g).get("route_split"))} for k, g in df.groupby(df["run_label"].fillna(""))}).T,
             width="stretch")
