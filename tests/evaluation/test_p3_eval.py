import json
import sys

import pytest

from app.evaluation.p3_eval import ARMS_P3, Row, compare, run_arm, slice_rows, summarize
from app.security.auth import Principal
from tests.integration.test_project3 import Q, READER, T, ScriptedLLM, make, settings

QS = [{"id": "q1", "question": Q, "answer": "Lisbon", "type": "bridge", "level": "hard",
       "gold_document_ids": [f"{T}/film", f"{T}/dana"]},
      {"id": "s1", "question": "Where was Omar Vale born?", "answer": "Cairo", "type": "single", "level": "single",
       "gold_document_ids": [f"{T}/comet"]}]


def mk(arm):
    cfg = {k: v for k, v in ARMS_P3[arm].items()}
    s = settings(**{"system": "project3", **{k: v for k, v in cfg.items() if k != "system"}}) if cfg["system"] == "project3" else settings(system="project2", top_k=2)
    svc, *_ = make(s=s)
    if cfg["system"] == "project2":
        svc.hybrid = None
    return svc


def test_hybrid_beats_vector_on_supporting_fact_recall_and_pays_for_it():
    v = run_arm("vector", mk("vector"), READER, QS)
    h = run_arm("hybrid", mk("hybrid"), READER, QS)
    assert v[0].sf_recall == 0.5 and h[0].sf_recall == 1.0 and h[0].all_found == 1.0 and v[0].all_found == 0.0
    assert h[0].cand_recall == 1.0 and v[0].cand_recall is None
    assert h[0].cost_usd > v[0].cost_usd  # question-entity extraction call is counted
    assert h[0].em == 1.0  # "Lisbon [1]" scored AFTER stripping the citation marker


def test_summary_slices_and_compare_claim_logic():
    v, h = run_arm("vector", mk("vector"), READER, QS), run_arm("hybrid", mk("hybrid"), READER, QS)
    s = summarize(h)
    assert set(s) == {"all", "multi_hop", "bridge", "single", "multi_hop:hard"} and s["multi_hop"]["n"] == 1
    c = compare(v, h)
    assert c["multi_hop"]["sf_recall"]["delta"] == pytest.approx(0.5) and "NOT supported" in c["multi_hop"]["claim"]  # n=1: CI can't prove it
    mkr = lambda f1, arm: [Row(arm, str(i), "bridge", "hard", "q", "g", "a", f1, f1, 1, 1, None, 0, 1, 1, None) for i in range(40)]
    assert "SUPPORTED" in compare(mkr(0.0, "a"), mkr(1.0, "b"))["multi_hop"]["claim"] and "NOT" not in compare(mkr(0.0, "a"), mkr(1.0, "b"))["multi_hop"]["claim"]
    harm = [Row("b", str(i), "single", "single", "q", "g", "a", 0.0, 0.0, 1, 1, None, 0, 1, 1, None) for i in range(40)]
    ref = [Row("a", str(i), "single", "single", "q", "g", "a", 1.0, 1.0, 1, 1, None, 0, 1, 1, None) for i in range(40)]
    assert "NOT supported" in compare(ref, harm)["single"]["claim"]  # a real collapse on single-hop must be flagged


def test_run_p3_eval_cli_end_to_end(tmp_path, monkeypatch):
    import scripts.run_p3_eval as ev
    qf = tmp_path / "q.jsonl"
    qf.write_text("\n".join(json.dumps(q) for q in QS) + "\n")
    monkeypatch.setattr(ev, "get_settings", lambda: settings(system="project3"))
    monkeypatch.setattr(ev, "build_service", lambda s: make(s=s)[0] if s.system == "project3" else (lambda svc: (setattr(svc, "hybrid", None), svc)[1])(make(s=s)[0]))
    monkeypatch.setattr(sys, "argv", ["x", "--questions", str(qf), "--arms", "vector,vector_rerank,hybrid", "--out-dir", str(tmp_path)])
    ev.main()
    sm = json.loads((tmp_path / "project3_summary.json").read_text())
    assert set(sm["arms"]) == {"vector", "vector_rerank", "hybrid"} and "vs_vector" in sm["arms"]["hybrid"]
    rep = (tmp_path / "project3_report.md").read_text()
    assert "STUB / HASH / LEXICAL RUN" in rep and "Indexing cost" in rep and "graph_build_report.json not found" in rep
    assert (tmp_path / "project3.csv").exists()
