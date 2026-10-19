import csv
import json
import sys

from app.evaluation.synthetic_tenants import build_corpus


def test_run_evaluation_end_to_end(service, tmp_path, monkeypatch):
    import scripts.run_evaluation as ev
    ev_set = tmp_path / "e.jsonl"
    ev_set.write_text("\n".join(json.dumps({
        "question": f"{c} product overview customers countries sales ∈", "reference_answer": "x",
        "source_document": f"{t}/product-overview", "validated": True})
        for t, c in [("helios", "Helios Robotics"), ("borealis", "Borealis Foods")]) + "\n")
    monkeypatch.setattr(ev, "build_service", lambda s: service)
    monkeypatch.setattr(sys, "argv", ["x", "--system", "project2", "--eval-set", str(ev_set), "--tenant", "helios",
                                      "--user", "employee@helios", "--roles", "employee", "--no-judge",
                                      "--out-dir", str(tmp_path)])
    ev.main()
    with (tmp_path / "project2.csv").open(encoding="utf-8", newline="") as f:
        csv_text = f.read()
    assert "∈" in csv_text
    rows = list(csv.DictReader(csv_text.splitlines()))
    summ = json.loads((tmp_path / "project2_summary.json").read_text())
    assert len(rows) == 2
    assert rows[0]["retrieval_hit"] == "1.0"   # helios user finds helios doc
    assert rows[1]["retrieval_hit"] == "0.0"   # ...and CANNOT retrieve borealis doc
    assert summ["config"]["llm_backend"] and summ["metrics"]["answer_correctness_llm_judge"] is None
