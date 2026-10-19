"""Loader tests use a SYNTHETIC fixture in the real HotpotQA schemas (HF + official). It is not real data."""
import json

from scripts.load_hotpotqa import build, normalize_example, sample, slug

HF = {"id": "q1", "question": "Where was the director of Film Orbit born?", "answer": "Lisbon", "type": "bridge", "level": "hard",
      "supporting_facts": {"title": ["Film Orbit", "Dana Reed", "Dana Reed"], "sent_id": [0, 0, 1]},
      "context": {"title": ["Film Orbit", "Dana Reed", "Noise"],
                  "sentences": [["Film Orbit is a film ∈.", " Directed by Dana Reed."], ["Dana Reed was born in Lisbon."], ["Unrelated."]]}}
OFFICIAL = {"_id": "q2", "question": "Which is older, A or B?", "answer": "A", "type": "comparison", "level": "easy",
            "supporting_facts": [["A", 0], ["B", 0]], "context": [["A", ["A is old."]], ["B", ["B is new."]], ["C", ["Noise."]]]}


def test_both_schemas_normalise_identically():
    h, o = normalize_example(HF), normalize_example(OFFICIAL)
    assert h["supporting_titles"] == ["Film Orbit", "Dana Reed"] and h["paragraphs"]["Film Orbit"] == "Film Orbit is a film ∈. Directed by Dana Reed."
    assert o["id"] == "q2" and o["supporting_titles"] == ["A", "B"] and set(o["paragraphs"]) == {"A", "B", "C"}


def test_build_writes_corpus_manifest_and_gold_ids(tmp_path):
    stats = build([normalize_example(HF), normalize_example(OFFICIAL)], "hotpot", tmp_path)
    man = json.loads((tmp_path / "manifest.json").read_text())
    ids = {m["document_id"] for m in man}
    q = [json.loads(l) for l in (tmp_path / "questions.jsonl").read_text().splitlines()]
    assert stats["n_documents"] == 6 and stats["n_questions"] == 2 and stats["types"] == {"bridge": 1, "comparison": 1}
    assert set(q[0]["gold_document_ids"]) <= ids and "hotpot/Dana_Reed" in ids
    assert (tmp_path / "docs" / "Film_Orbit.md").read_text(encoding="utf-8").startswith("Film Orbit: Film Orbit is a film ∈.")
    assert (tmp_path / "docs" / "Dana_Reed.md").read_text(encoding="utf-8").startswith("Dana Reed: Dana Reed was born in Lisbon.")
    assert all(m["tenant_id"] == "hotpot" and m["allowed_roles"] == ["reader"] for m in man)  # ingest-compatible, ACL'd


def test_question_with_missing_gold_paragraph_is_skipped(tmp_path):
    bad = normalize_example({**OFFICIAL, "_id": "q3", "supporting_facts": [["A", 0], ["Missing", 0]]})
    assert build([bad], "hotpot", tmp_path)["n_skipped_missing_gold_paragraph"] == 1


def test_sampling_is_deterministic_and_stratified():
    exs = [{"id": i, "type": "bridge" if i % 4 else "comparison"} for i in range(400)]
    a, b = sample(list(exs), 40, 7), sample(list(exs), 40, 7)
    assert [e["id"] for e in a] == [e["id"] for e in b] and len(a) == 40
    assert 8 <= sum(e["type"] == "comparison" for e in a) <= 12  # ~25% like the source


def test_slug():
    assert slug("AC/DC: Live (album)") == "AC_DC__Live_(album)" and slug("Dana Reed") == "Dana_Reed"
