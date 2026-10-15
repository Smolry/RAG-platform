# Resume lines (PDF templates). NEVER fill X/Y/Z/N until `results/*.json|csv` contain them.
- **Project 2:** "Built a multi-tenant RAG service with pre-retrieval ACL filtering and audit logging, verified by an **N**-query adversarial test suite in CI with zero cross-tenant leaks and under **X** ms filtering overhead."
  Sources: `results/project2_security.json` (`total_queries`, `leaks`), `results/project2_measurements.json` (`filter_overhead_ms`).
- **Project 1:** "Added semantic caching and cost-aware model routing to a RAG service, cutting LLM cost by **X%** and p95 latency by **Y%** with no drop in answer quality on a **Z**-question eval set." 
  Sources: `results/project1_summary.json` → `arms.full.vs_always_large` (cost_reduction_pct, p95_latency_change_pct, quality_delta.claim). **Only use "no drop in answer quality" if the report line says SUPPORTED.**
- **Project 3:** "Built a GraphRAG system combining knowledge graph traversal with vector retrieval, raising multi-hop answer F1 from **X** to **Y** and supporting-fact recall by **Z** points over a vector-only baseline on HotpotQA." *(pending Project 3)*
