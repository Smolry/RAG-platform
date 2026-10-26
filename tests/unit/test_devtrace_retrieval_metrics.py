import pytest

from app.evaluation.devtrace.retrieval_metrics import (all_required_found, collapse, hit_at_k, mean_defined, precision_at_k,
                                                       recall_at_k, reciprocal_rank, source_coverage, version_correctness)

R = ["d1", "d2", "d3", "d4", "d5"]


def test_recall_precision_hit_hand_computed():
    rel = {"d2", "d4", "d9"}
    assert recall_at_k(rel, R, 1) == 0.0 and recall_at_k(rel, R, 2) == pytest.approx(1 / 3) and recall_at_k(rel, R, 5) == pytest.approx(2 / 3)
    assert precision_at_k(rel, R, 2) == pytest.approx(1 / 2) and precision_at_k(rel, R, 5) == pytest.approx(2 / 5)
    assert hit_at_k(rel, R, 1) == 0.0 and hit_at_k(rel, R, 2) == 1.0


def test_precision_divides_by_k_even_with_fewer_results():
    assert precision_at_k({"a"}, ["a"], 5) == pytest.approx(1 / 5)


def test_reciprocal_rank():
    assert reciprocal_rank({"d3"}, R) == pytest.approx(1 / 3)
    assert reciprocal_rank({"d1", "d3"}, R) == 1.0
    assert reciprocal_rank({"zz"}, R) == 0.0 and reciprocal_rank({"d3"}, R, k=2) == 0.0


def test_duplicates_are_collapsed_so_scores_cannot_exceed_one():
    ranked = ["pageA", "pageA", "pageA", "pageB"]
    assert collapse(ranked) == ["pageA", "pageB"]
    assert precision_at_k({"pageA"}, ranked, 3) == pytest.approx(1 / 3) and recall_at_k({"pageA", "pageB"}, ranked, 2) == 1.0
    assert reciprocal_rank({"pageB"}, ranked) == pytest.approx(1 / 2)          # rank among distinct keys, not raw positions


def test_cases_without_relevant_labels_are_not_applicable_not_zero():
    for f in (lambda: recall_at_k(set(), R, 3), lambda: precision_at_k(set(), R, 3), lambda: hit_at_k(set(), R, 3),
              lambda: reciprocal_rank(set(), R), lambda: all_required_found(set(), R, 3), lambda: source_coverage(set(), R, 3)):
        assert f() is None
    assert mean_defined([1.0, None, 0.0, None]) == (0.5, 2) and mean_defined([None, None]) == (None, 0) and mean_defined([]) == (None, 0)


def test_multi_source_metrics():
    assert source_coverage({"d1", "d5"}, R, 3) == 0.5 and all_required_found({"d1", "d5"}, R, 3) == 0.0
    assert all_required_found({"d1", "d5"}, R, 5) == 1.0


def test_invalid_k_rejected():
    for f in (recall_at_k, precision_at_k, hit_at_k, all_required_found):
        with pytest.raises(ValueError):
            f({"a"}, ["a"], 0)


def test_version_correctness():
    items = [{"library": "fastapi", "library_version": "0.101.0", "version_kind": "introduced_in"},
             {"library": "fastapi", "library_version": "0.120.0", "version_kind": "introduced_in"},      # out of range
             {"library": "pydantic", "library_version": "2.5.0", "version_kind": "introduced_in"},       # wrong library
             {"library": "fastapi", "library_version": "0.162.0", "version_kind": "docs_as_of"}]         # docs pages are not judged
    assert version_correctness(items, "fastapi", "0.100.0", "0.110.0") == pytest.approx(1 / 3)
    assert version_correctness(items[3:], "fastapi", "0.1.0", None) is None
    assert version_correctness([], "fastapi", None, None) is None
