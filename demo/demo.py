"""Standalone Streamlit demo for saved RAG experiment results."""
import json
from pathlib import Path

import streamlit as st


def find_repo_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    for candidate in (script_dir, *script_dir.parents):
        if (candidate / "results" / "baseline_summary.json").is_file() and (
            candidate / "docs" / "results"
        ).is_dir():
            return candidate
    raise FileNotFoundError(
        f"Could not find demo artifacts above {script_dir}; expected results/baseline_summary.json "
        "and docs/results/ in the repository."
    )


ROOT = find_repo_root()


@st.cache_data
def load_result(filename: str) -> dict:
    return json.loads((ROOT / "results" / filename).read_text(encoding="utf-8"))


baseline = load_result("baseline_summary.json")
security = load_result("project2_security.json")
project1 = load_result("project1_summary.json")
router_stress = load_result("router_eval_stress.json")
project3 = load_result("project3_summary.json")
graph_build = load_result("graph_build_report.json")


def show_chart(filename: str, caption: str, alt: str) -> None:
    st.image(ROOT / "docs" / "results" / filename, caption=caption, alt=alt, width="stretch")


def metric_row(metrics: list[tuple[str, str]]) -> None:
    for column, (label, value) in zip(st.columns(len(metrics)), metrics):
        column.metric(label, value)


st.set_page_config(page_title="RAG platform experiment demo", page_icon=":material/analytics:", layout="wide")
st.title("RAG platform: experiment demo")
st.caption("Baseline retrieval, tenant security, semantic caching and routing, and hybrid GraphRAG")
st.warning(
    "These are saved benchmark results, not live traffic. They come from modest, curated evaluations "
    "and individual runs; they do not guarantee production performance. Cost estimates use the "
    "repository's sample price table, not provider invoices.",
    icon=":material/info:",
)

baseline_tab, security_tab, cache_tab, graph_tab = st.tabs(
    ["Baseline RAG", "Tenant security", "Caching and routing", "GraphRAG"]
)

with baseline_tab:
    st.header("Baseline retrieval and answer quality")
    metrics = baseline["metrics"]
    metric_row(
        [
            ("Questions", f"{baseline['n_questions']:,}"),
            ("Retrieval hit rate", f"{metrics['retrieval_hit_rate']:.1%}"),
            ("LLM-judged answer correctness", f"{metrics['answer_correctness_llm_judge']:.1%}"),
            ("Citation correctness", f"{metrics['citation_correctness']:.1%}"),
        ]
    )
    metric_row(
        [
            ("p50 latency", f"{metrics['p50_latency_ms'] / 1000:.2f} s"),
            ("p95 latency", f"{metrics['p95_latency_ms'] / 1000:.2f} s"),
            ("Mean cost / request", f"${metrics['mean_cost_usd_per_request']:.6f}"),
            ("Total measured cost", f"${metrics['total_cost_usd']:.4f}"),
        ]
    )
    st.caption(
        "Retrieval found a supporting document for most questions, while judged answer and citation "
        "quality leave room for improvement. LLM-judge correctness is a proxy, not a human-quality score."
    )
    show_chart(
        "phase0-baseline.png",
        "Saved baseline benchmark",
        "Baseline retrieval hit rate, judged answer quality, latency, and cost",
    )

with security_tab:
    st.header("Tenant authorization under a security suite")
    metric_row(
        [
            ("Queries tested", f"{security['total_queries']:,}"),
            ("Cross-tenant leaks", f"{security['cross_tenant_leaks']:,}"),
            ("Adversarial pass rate", f"{security['adversarial_pass_rate']:.0%}"),
            ("Authorized chunks returned", f"{security['authorized_chunks_returned']:,}"),
        ]
    )
    st.info(
        "The suite observed zero leaks in its 840 queries. This is evidence about the implemented "
        "fixture policies and attack cases, not a formal security proof or coverage of every attack."
    )
    show_chart(
        "project2-security.png",
        "Saved tenant-security and phase comparison results",
        "Tenant-security suite results, revocation latency, and descriptive baseline comparison",
    )

with cache_tab:
    st.header("Semantic caching and model routing")
    arms = project1["arms"]
    arm_labels = {
        "always_large": "Always large",
        "routing_only": "Routing only",
        "cache_only": "Cache only",
        "full": "Cache + routing",
    }
    rows = []
    for key, label in arm_labels.items():
        metrics = arms[key]["overall"]
        rows.append(
            {
                "Benchmark arm": label,
                "Cost / request": f"${metrics['cost_per_request_usd']:.5f}",
                "p95 latency": f"{metrics['p95_latency_ms'] / 1000:.2f} s",
                "Cache hit rate": f"{metrics['cache_hit_rate']:.1%}",
                "Judged answer quality": f"{metrics['quality']:.3f}",
            }
        )
    st.table(rows)
    st.caption("Project 1 benchmark: 60 questions, each evaluated as original, paraphrase, and repeat.")
    show_chart(
        "project1-benchmark.png",
        "Saved cost, latency, cache-hit, and quality comparison",
        "Benchmark cost and p95 latency trade-offs across four cache and routing arms",
    )

    stress = router_stress["confusion"]
    metric_row(
        [
            ("Router stress accuracy", f"{router_stress['accuracy']:.0%}"),
            ("Stress questions", f"{router_stress['n']}"),
            ("Complex-question recall", f"{router_stress['complex_recall']:.0%}"),
            ("Missed complex questions", f"{stress['fn_risky']} of {stress['fn_risky'] + stress['tp']}"),
        ]
    )
    st.warning(
        "The router reached only 25% accuracy on this 12-query stress set and missed all six complex "
        "questions. Its stronger author-written dev/test scores do not establish reliable routing on "
        "arbitrary traffic. Caching also traded cost and latency savings for false hits: the full "
        "benchmark arm had 11 cache hits judged wrong."
    )

with graph_tab:
    st.header("Vector-only versus hybrid GraphRAG")
    all_slice = project3["arms"]
    vector_all = all_slice["vector"]["by_slice"]["all"]
    hybrid_all = all_slice["hybrid"]["by_slice"]["all"]
    vector_multi = all_slice["vector"]["by_slice"]["multi_hop"]
    hybrid_multi = all_slice["hybrid"]["by_slice"]["multi_hop"]
    metric_row(
        [
            ("Evaluation questions", f"{project3['n_questions']:,}"),
            ("Overall F1: vector → hybrid", f"{vector_all['f1']:.1%} → {hybrid_all['f1']:.1%}"),
            (
                "Multi-hop F1: vector → hybrid",
                f"{vector_multi['f1']:.1%} → {hybrid_multi['f1']:.1%}",
            ),
            (
                "Multi-hop supporting-fact recall",
                f"{vector_multi['sf_recall']:.1%} → {hybrid_multi['sf_recall']:.1%}",
            ),
        ]
    )

    comparison_rows = []
    for key, label in (("vector", "Vector only"), ("hybrid", "Hybrid GraphRAG")):
        metrics = all_slice[key]["by_slice"]["multi_hop"]
        comparison_rows.append(
            {
                "Multi-hop retrieval arm": label,
                "Questions": metrics["n"],
                "F1": f"{metrics['f1']:.1%}",
                "Supporting-fact recall": f"{metrics['sf_recall']:.1%}",
                "Cost / query": f"${metrics['cost_per_query_usd']:.5f}",
                "p95 latency": f"{metrics['p95_latency_ms'] / 1000:.2f} s",
            }
        )
    st.table(comparison_rows)
    indexing = project3["indexing"]
    metric_row(
        [
            ("Graph indexing cost", f"${graph_build['indexing_cost_usd']:.4f}"),
            ("Indexed chunks", f"{indexing['n_chunks']:,}"),
            ("Graph relations", f"{indexing['relations']:,}"),
        ]
    )
    st.caption(
        "The multi-hop comparison uses 120 validated HotpotQA questions. Supporting-fact recall is "
        "paragraph-level; the dataset and distractor setup are not directly comparable to open-domain results."
    )
    show_chart(
        "project3-graphrag.png",
        "Saved GraphRAG evaluation",
        "Answer F1 across vector, reranked, and hybrid GraphRAG retrieval arms",
    )

st.divider()
st.caption(
    "Experiment snapshots: "
    f"baseline {baseline['timestamp'][:10]} · "
    f"security {security['timestamp'][:10]} · "
    f"cache and routing {project1['timestamp'][:10]} · "
    f"GraphRAG {project3['timestamp'][:10]}"
)
