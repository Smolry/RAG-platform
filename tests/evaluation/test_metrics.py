import pytest

from app.evaluation.metrics import (exact_match, f1_score, parse_judge, percentile, retrieval_hit,
                                    supporting_fact_recall)


def test_em_f1():
    assert exact_match("The Eiffel Tower.", "eiffel tower") == 1.0
    assert exact_match("Paris", "London") == 0.0
    assert f1_score("born in Paris France", "Paris") == pytest.approx(2 * 0.25 * 1 / 1.25)
    assert f1_score("", "x") == 0.0


def test_retrieval_and_sf_recall():
    assert retrieval_hit("d1", ["d2", "d1"]) == 1.0 and retrieval_hit("d1", ["d2"]) == 0.0
    assert supporting_fact_recall({"a", "b"}, {"a"}) == 0.5
    assert supporting_fact_recall(set(), {"a"}) == 0.0


def test_percentile():
    xs = list(range(1, 101))
    assert percentile(xs, 50) == pytest.approx(50.5)
    assert percentile(xs, 95) == pytest.approx(95.05)


def test_parse_judge():
    assert parse_judge('{"correct": true, "reason": "ok"}')[0] == 1.0
    assert parse_judge('noise {"correct": false, "reason": "no"} noise')[0] == 0.0
    assert parse_judge("garbage")[0] == 0.0
