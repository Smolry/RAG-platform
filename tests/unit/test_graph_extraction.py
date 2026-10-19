import json

import pytest

from app.generation.llm import LLMResult, PriceTable
from app.graph.extraction import ExtractStats, Rejections, extract_chunk, grounded, validate_extraction

TXT = "Film Orbit was directed by Dana Reed. Dana Reed was born in Lisbon, Portugal."


def v(d, text=TXT):
    rej = Rejections()
    return validate_extraction(json.dumps(d), "c1", text, rej), rej


GOOD = {"entities": [{"name": "Film Orbit", "type": "WORK"}, {"name": "Dana Reed", "type": "PERSON"},
                     {"name": "Lisbon", "type": "LOCATION"}],
        "relations": [{"source": "Film Orbit", "type": "DIRECTED_BY", "target": "Dana Reed"},
                      {"source": "Dana Reed", "type": "BORN_IN", "target": "Lisbon"}]}


def test_valid_extraction_sets_evidence_chunk_id_from_our_code():
    d = {**GOOD, "relations": [{**r, "evidence_chunk_id": "FORGED"} for r in GOOD["relations"]]}  # LLM cannot choose evidence
    ex, rej = v(d)
    assert len(ex.entities) == 3 and len(ex.relations) == 2 and not rej.counts
    assert {r.evidence_chunk_id for r in ex.relations} == {"c1"}


def test_arbitrary_entity_and_relation_types_rejected_not_invented():
    ex, rej = v({"entities": [{"name": "Dana Reed", "type": "WIZARD"}, {"name": "Lisbon", "type": "city"}],
                 "relations": [{"source": "Dana Reed", "type": "TELEPORTS_TO", "target": "Lisbon"}]})
    assert rej.counts["bad_entity_type"] == 1 and rej.counts["bad_relation_type"] == 1
    assert [e.type for e in ex.entities] == ["LOCATION"]  # 'city' alias mapped into the fixed enum
    assert ex.relations == []


def test_hallucinated_entity_rejected_and_dangling_relation_dropped():
    ex, rej = v({"entities": [{"name": "Dana Reed", "type": "PERSON"}, {"name": "Atlantis", "type": "LOCATION"}],
                 "relations": [{"source": "Dana Reed", "type": "BORN_IN", "target": "Atlantis"},
                               {"source": "Dana Reed", "type": "BORN_IN", "target": "Dana Reed"}]})
    assert rej.counts["ungrounded_entity"] == 1 and rej.counts["dangling_relation"] == 1 and rej.counts["self_relation"] == 1
    assert [e.name for e in ex.entities] == ["Dana Reed"] and ex.relations == []


def test_self_relation_and_dedupe():
    ex, rej = v({"entities": [{"name": "Dana Reed", "type": "PERSON"}, {"name": "dana reed", "type": "PERSON"}],
                 "relations": [{"source": "Dana Reed", "type": "SPOUSE_OF", "target": "dana reed"}]})
    assert len(ex.entities) == 1 and rej.counts["self_relation"] == 1 and not ex.relations


@pytest.mark.parametrize("raw", ["not json at all", "[1,2,3]", '{"entities": "x"}', "", "{broken"])
def test_garbage_never_raises(raw):
    ex = validate_extraction(raw, "c1", TXT, Rejections())
    assert ex.entities == [] and ex.relations == []


def test_bad_items_do_not_poison_good_ones():
    ex, rej = v({"entities": [GOOD["entities"][1], 42, {"name": "", "type": "PERSON"}, {"type": "PERSON"}], "relations": [7]})
    assert [e.name for e in ex.entities] == ["Dana Reed"] and rej.counts["bad_entity_schema"] >= 3 and rej.counts["bad_relation_schema"] == 1


def test_grounding_is_accent_and_punctuation_insensitive():
    assert grounded("Sao Paulo", "He lived in São Paulo, Brazil.") and not grounded("Rio", "He lived in São Paulo.")
    assert not grounded("Dan", "Dana went home.")  # whole-token match only


def test_extract_chunk_retries_invalid_json_and_tracks_cost():
    class Flaky:
        n = 0
        def complete(self, messages, model, temperature=0.0):
            Flaky.n += 1
            return LLMResult("sorry no json" if Flaky.n == 1 else json.dumps(GOOD), 100, 50, model)
    stats, rej = ExtractStats(), Rejections()
    ex = extract_chunk(Flaky(), "m", PriceTable({"m": {"input_per_1m": 1.0, "output_per_1m": 2.0}}), "c1", TXT, stats, rej)
    assert len(ex.relations) == 2 and stats.calls == 2 and stats.retries == 1
    assert stats.cost_usd == pytest.approx(2 * (100 * 1 + 50 * 2) / 1e6)
