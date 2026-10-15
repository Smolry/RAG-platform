import pytest

from app.routing.router import RuleRouter


@pytest.fixture(scope="module")
def r():
    return RuleRouter.from_yaml("configs/routing.yaml")


@pytest.mark.parametrize("q", ["What is Redis?", "Who created Git?", "What port does PostgreSQL use?"])
def test_simple_queries(r, q):
    assert r.classify(q).route == "simple"


@pytest.mark.parametrize("q", [
    "Why does Raft need a leader and how does it differ from Paxos?",
    "Compare Kafka and RabbitMQ",
    "Explain the trade-off between consistency and availability",
])
def test_complex_queries(r, q):
    assert r.classify(q).route == "complex"


def test_retrieval_signals_only_when_scores_given(r):
    q = "What is Redis?"
    assert r.classify(q).score == 0
    assert r.classify(q, [0.1, 0.09, 0.09]).score == 2  # low top score + flat spread
    assert r.classify(q, [0.1, 0.09, 0.09]).route == "complex"


def test_keyword_whole_word_only(r):
    assert r.classify("Which whystle is loud?").signals["keywords"] == []  # 'why' inside a word must not fire


def test_escalation_signals(r):
    assert r.needs_escalation("I don't know based on the available documents.", 0, [0.9], 0.3) == (True, "idk_phrase")
    assert r.needs_escalation("It is X.", 0, [0.9], 0.3) == (True, "no_citation")
    assert r.needs_escalation("It is X [1].", 1, [0.1], 0.3) == (True, "low_retrieval_score")
    assert r.needs_escalation("It is X [1].", 1, [0.9], 0.3) == (False, "")
