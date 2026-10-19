import json
import sys

import pytest

from app.evaluation.benchmark import ARMS, Row, compare, paired_bootstrap, run_arm, summarize_arm, workload
from tests.conftest import ScriptedLLM, build_p1, principal_for


class JudgeAlwaysCorrect:
    def complete(self, messages, model, temperature=0.0):
        from app.generation.llm import LLMResult
        return LLMResult('{"correct": true, "reason": "x"}', 1, 1, model)


ITEMS = [
    {"question": "What is the expense limit?", "paraphrase": "what's the limit for expenses", "reference_answer": "250",
     "source_document": "helios/expense-policy"},
    {"question": "How many days of paid leave?", "paraphrase": "paid leave days per year?", "reference_answer": "24",
     "source_document": "helios/employee-handbook"},
]


def test_workload_passes():
    w = workload(ITEMS)
    assert [p for p, _, _ in w] == ["original"] * 2 + ["paraphrase"] * 2 + ["repeat"] * 2
    assert len(workload([{**ITEMS[0], "paraphrase": ""}])) == 2  # no paraphrase -> pass skipped


def _run(p1_settings, corpus, arm):
    svc = build_p1(p1_settings.model_copy(update=ARMS[arm]), corpus, ScriptedLLM())
    return run_arm(arm, svc, principal_for(corpus[1], "employee@helios"), ITEMS, JudgeAlwaysCorrect(), "judge")


def test_arms_behave_as_labelled(p1_settings, corpus):
    large, full = _run(p1_settings, corpus, "always_large"), _run(p1_settings, corpus, "full")
    assert not any(r.cache_hit for r in large) and {r.model for r in large} == {"gpt-4o"}
    assert any(r.cache_hit for r in full if r.pass_name == "repeat")  # repeats must hit
    sl, sf = summarize_arm(large), summarize_arm(full)
    assert sf["overall"]["cost_per_request_usd"] < sl["overall"]["cost_per_request_usd"]
    assert sf["repeat"]["cache_hit_rate"] == 1.0 and sl["repeat"]["cache_hit_rate"] == 0.0
    c = compare(large, full)
    assert c["cost_reduction_pct"] > 0 and c["quality_delta"]["mean"] == 0 and "SUPPORTED" in c["quality_delta"]["claim"]


def test_bootstrap_ci_and_claim_logic():
    mean, lo, hi = paired_bootstrap([0.0] * 50)
    assert mean == lo == hi == 0
    mean, lo, hi = paired_bootstrap([-1.0, 0.0] * 5)  # large noisy drop
    assert mean == pytest.approx(-0.5) and lo < -0.05
    mk = lambda c: [Row("a", "original", i, "q", "a", 1.0, c[i], False, None, False, "m", 1.0, 10.0) for i in range(len(c))]
    drop = compare(mk([1.0] * 10), mk([1.0] * 6 + [0.0] * 4))
    assert "NOT supported" in drop["quality_delta"]["claim"]  # a real 40-pt drop must never read as "no drop"


def test_run_benchmark_cli_end_to_end(p1_settings, corpus, tmp_path, monkeypatch):
    import scripts.run_benchmark as rb
    ev = tmp_path / "e.jsonl"
    ev.write_text("\n".join(json.dumps(i) for i in ITEMS) + "\n")
    monkeypatch.setattr(rb, "get_settings", lambda: p1_settings)
    monkeypatch.setattr(rb, "build_service", lambda s: build_p1(s, corpus, ScriptedLLM()))
    monkeypatch.setattr(rb, "RecordingLLM", JudgeAlwaysCorrect)
    monkeypatch.setattr(rb, "LiteLLMClient", JudgeAlwaysCorrect)
    monkeypatch.setattr(sys, "argv", ["x", "--eval-set", str(ev), "--tenant", "helios", "--user", "employee@helios",
                                      "--roles", "employee", "--out-dir", str(tmp_path)])
    rb.main()
    s = json.loads((tmp_path / "project1_summary.json").read_text())
    assert set(s["arms"]) == set(ARMS) and "vs_always_large" in s["arms"]["full"]
    assert (tmp_path / "project1.csv").exists() and "Project 1 benchmark report" in (tmp_path / "project1_report.md").read_text()
