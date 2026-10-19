"""Pure aggregation over the request_log DataFrame (unit-tested). The dashboard shows ONLY what is in the log."""
from __future__ import annotations

import pandas as pd


def _p(s: pd.Series, q: float) -> float:
    return float(s.quantile(q / 100, interpolation="linear")) if len(s) else float("nan")


def headline(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    routed = df[df["route"].notna()]
    q = df["quality_score"].dropna()
    return {"n": len(df), "cost_per_request": float(df["cost_usd"].mean()), "total_cost": float(df["cost_usd"].sum()),
            "cache_hit_rate": float(df["cache_hit"].astype(bool).mean()),
            "p50_ms": _p(df["latency_ms"], 50), "p95_ms": _p(df["latency_ms"], 95),
            "quality": float(q.mean()) if len(q) else None, "n_judged": int(len(q)),
            "escalation_rate": float(routed["escalated"].astype(bool).mean()) if len(routed) else None,
            "route_split": routed["route"].value_counts(normalize=True).to_dict() if len(routed) else {}}


def by_route(df: pd.DataFrame) -> pd.DataFrame:
    d = df.assign(route=df["route"].fillna("cache/none"))
    return (d.groupby("route").agg(requests=("request_id", "count"), cost_per_request=("cost_usd", "mean"),
                                   p95_ms=("latency_ms", lambda s: _p(s, 95)), quality=("quality_score", "mean"),
                                   n_judged=("quality_score", "count")).reset_index())


def over_time(df: pd.DataFrame, freq: str = "h") -> pd.DataFrame:
    if df.empty:
        return df
    d = df.assign(ts=pd.to_datetime(df["ts"], utc=True)).set_index("ts").sort_index()
    return (d.resample(freq).agg(requests=("request_id", "count"), cost_per_request=("cost_usd", "mean"),
                                 cache_hit_rate=("cache_hit", lambda s: s.astype(bool).mean()),
                                 p50_ms=("latency_ms", lambda s: _p(s, 50)), p95_ms=("latency_ms", lambda s: _p(s, 95)),
                                 quality=("quality_score", "mean")).dropna(subset=["cost_per_request"]).reset_index())
