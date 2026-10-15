import pandas as pd
import pytest

from dashboard.metrics import by_route, headline, over_time


def frame():
    return pd.DataFrame({
        "request_id": list("abcd"), "ts": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 10:30", "2026-01-01 11:00", "2026-01-01 11:10"], utc=True),
        "cost_usd": [0.0, 0.2, 0.4, 0.0], "latency_ms": [10.0, 100.0, 200.0, 20.0], "cache_hit": [True, False, False, True],
        "route": [None, "simple", "complex", None], "escalated": [False, True, False, False],
        "quality_score": [1.0, 0.0, 1.0, None]})


def test_headline_matches_hand_computation():
    h = headline(frame())
    assert h["n"] == 4 and h["cost_per_request"] == pytest.approx(0.15) and h["cache_hit_rate"] == 0.5
    assert h["p50_ms"] == pytest.approx(60.0) and h["quality"] == pytest.approx(2 / 3) and h["n_judged"] == 3
    assert h["route_split"] == {"simple": 0.5, "complex": 0.5} and h["escalation_rate"] == 0.5


def test_by_route_and_over_time():
    br = by_route(frame()).set_index("route")
    assert br.loc["cache/none", "requests"] == 2 and br.loc["simple", "quality"] == 0.0
    t = over_time(frame(), "h")
    assert list(t["requests"]) == [2, 2] and t["cache_hit_rate"].tolist() == [0.5, 0.5]


def test_empty():
    assert headline(frame().iloc[0:0]) == {"n": 0}
