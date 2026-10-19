import pytest

from app.graph.resolution import Mention, norm_key, resolve_entities, string_rule


def M(name, typ="PERSON", chunk="c1"):
    return Mention(name, typ, chunk)


def test_norm_key_drops_articles_accents_punct():
    assert norm_key("The Beatles") == "beatles" and norm_key("São  Paulo!") == "sao paulo"


def test_person_initials_merge_and_connect_chunks():
    r = resolve_entities([M("Dana Reed", chunk="c1"), M("D. Reed", chunk="c2"), M("Dana Reed", chunk="c3")])
    assert len(r.entities) == 1 and r.n_raw == 2
    assert r.resolve("D. Reed", "PERSON") == r.resolve("Dana Reed", "PERSON")
    assert r.entities[0].mention_count == 3 and r.merges[0]["rule"] == "person_initials"


def test_different_people_do_not_merge():
    r = resolve_entities([M("John Smith"), M("Jane Smith"), M("John Smyth")])
    assert len(r.entities) == 3


def test_bare_surname_never_merges_into_full_name():
    assert len(resolve_entities([M("Dana Reed"), M("Reed")]).entities) == 2


def test_ambiguous_initial_is_left_unmerged_and_logged():
    r = resolve_entities([M("John Smith", chunk="a"), M("John Smith", chunk="b"), M("Jane Smith", chunk="c"),
                          M("Jane Smith", chunk="d"), M("J. Smith", chunk="e")])
    assert len(r.entities) == 3 and r.ambiguous and r.ambiguous[0]["name"] == "j smith"
    assert r.resolve("J. Smith", "PERSON") not in (r.resolve("John Smith", "PERSON"), r.resolve("Jane Smith", "PERSON"))


def test_types_never_merge():
    r = resolve_entities([M("Paris", "LOCATION"), M("Paris", "PERSON")])
    assert len(r.entities) == 2 and r.resolve("Paris", "LOCATION") != r.resolve("Paris", "PERSON")


def test_corporate_suffix_variants_merge():
    r = resolve_entities([M("Acme Inc.", "ORGANIZATION"), M("Acme", "ORGANIZATION", "c2"), M("Acme Corp", "ORGANIZATION", "c3")])
    assert len(r.entities) == 1


def test_embedding_gate_can_veto_a_string_match():
    veto = lambda texts: [[1.0, 0.0] if t.startswith("d reed") else [0.0, 1.0] for t in texts]  # orthogonal => sim 0 (resolver embeds normalised keys)
    assert len(resolve_entities([M("Dana Reed"), M("D. Reed", chunk="c2")], embed=veto).entities) == 2
    ok = lambda texts: [[1.0, 0.0] for _ in texts]
    assert len(resolve_entities([M("Dana Reed"), M("D. Reed", chunk="c2")], embed=ok).entities) == 1


def test_no_transitive_chaining_through_non_representatives():
    """A~B (92.6) and B~C (92.6) but A!~C (85.2). A is the representative (most chunks); B merges into A; C is compared to the
    REPRESENTATIVE only, so it must stay separate. (Mutation-tested: comparing to the latest member instead merges all three.)"""
    a, b, c = "northwind harbour logistics", "morxhwind harbour logistics", "morxhwind zarbour loqistics"
    r = resolve_entities([M(a, "ORGANIZATION", "1"), M(a, "ORGANIZATION", "2"), M(a, "ORGANIZATION", "3"),
                          M(b, "ORGANIZATION", "4"), M(b, "ORGANIZATION", "5"), M(c, "ORGANIZATION", "6")], fuzzy=92)
    assert len(r.entities) == 2
    assert r.resolve(a, "ORGANIZATION") == r.resolve(b, "ORGANIZATION") != r.resolve(c, "ORGANIZATION")
    assert [m["rule"] for m in r.merges] == ["fuzzy_ratio"]


def test_corporate_suffix_rule_is_not_applied_to_other_types():
    assert string_rule("acme inc", "acme", "ORGANIZATION", 92) == "corporate_suffix"
    assert string_rule("smith co", "smith", "PERSON", 92) is None
    assert string_rule("orbit ltd", "orbit", "WORK", 92) is None
    r = resolve_entities([M("Smith Co", "PERSON", "1"), M("Smith", "PERSON", "2")])
    assert len(r.entities) == 2


def test_string_rule_unit():
    assert string_rule("d reed", "dana reed", "PERSON", 92) == "person_initials"
    assert string_rule("reed", "dana reed", "PERSON", 92) is None
    assert string_rule("acme inc", "acme", "ORGANIZATION", 92) == "corporate_suffix"
    assert string_rule("lisbon", "lisboa", "LOCATION", 92) is None  # too short / below cutoff: precision first


def test_empty_and_blank_mentions():
    r = resolve_entities([M("   "), M("The")])
    assert r.entities == [] or all(e.name for e in r.entities)
