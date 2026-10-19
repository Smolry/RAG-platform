import pytest

from scripts.tune_cache_threshold import select, sweep

PAIRS = [{"should_hit": True}] * 3 + [{"should_hit": False}] * 3


def test_sweep_counts_and_selection_rule():
    sims = [0.99, 0.93, 0.88, 0.91, 0.80, 0.70]  # paraphrases first, then near-misses
    rows = {r["threshold"]: r for r in sweep(PAIRS, sims)}
    assert rows[0.85]["false_hits"] == 1 and rows[0.85]["false_misses"] == 0   # 0.91 near-miss gets through
    assert rows[0.92]["false_hits"] == 0 and rows[0.92]["false_misses"] == 1   # 0.88 paraphrase now missed
    chosen, rule = select(list(rows.values()))
    assert chosen["threshold"] == 0.92 and "zero false hits" in rule           # LOWEST threshold with zero false hits


def test_select_flags_unsafe_when_no_safe_threshold():
    sims = [0.99, 0.99, 0.99, 0.98, 0.5, 0.5]  # a near-miss at 0.98 -> only t>0.98 is safe, outside the sweep
    chosen, rule = select(sweep(PAIRS, sims))
    assert "UNSAFE" in rule and chosen["false_hits"] == 1


def test_sweep_range():
    ts = [r["threshold"] for r in sweep(PAIRS, [0.9] * 6)]
    assert ts[0] == 0.85 and ts[-1] == 0.97 and len(ts) == 13
