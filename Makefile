.PHONY: hotpot ingest-hotpot singlehop build-graph p3-ablate-resolution p3-eval p3 test-neo4j neo4j-up p1 tune-cache eval-router paraphrases bench dashboard demo up install corpus ingest-baseline api eval-baseline tenants ingest-tenants test security measure-p2
install:        ; pip install -r requirements.txt
up:             ; docker compose up -d
corpus:         ; python -m scripts.fetch_corpus
ingest-baseline:; python -m scripts.ingest --path data/raw --tenant acme --classification internal --roles employee,manager,hr,admin
api:            ; uvicorn app.api.main:app --reload --port 8000
eval-baseline:  ; python -m scripts.run_evaluation --system baseline --only-validated
tenants:        ; python -m scripts.generate_tenant_corpus
ingest-tenants: ; python -m scripts.ingest --manifest data/synthetic/manifest.json
test:           ; pytest -q
security:       ; pytest -q tests/security && python -m scripts.run_security_suite
measure-p2:     ; python -m scripts.measure_project2
p1: export SYSTEM = project1
p1:             ; uvicorn app.api.main:app --reload --port 8000
tune-cache:     ; python -m scripts.tune_cache_threshold
eval-router:    ; python -m scripts.eval_router --split dev
paraphrases:    ; python -m scripts.make_paraphrases
bench:          ; python -m scripts.run_benchmark --tenant acme --user eval@acme --roles employee --only-validated
dashboard:      ; streamlit run dashboard/app.py
demo:           ; streamlit run demo.py
neo4j-up:       ; docker compose up -d neo4j
hotpot:         ; python -m scripts.load_hotpotqa --n 120 --seed 7
ingest-hotpot:  ; python -m scripts.ingest --manifest data/hotpot/manifest.json
singlehop:      ; python -m scripts.make_singlehop_set --n 60
build-graph:    ; python -m scripts.build_graph --tenant hotpot --workers 2
# Resolution ablation. NOTE: all three steps write to the SAME Neo4j tenant graph (the last build wins), so run them in this order.
# The extraction cache makes the rebuilds free (no new LLM calls). Compare results/p3_noresolve/ with results/.
p3-ablate-resolution:
	python -m scripts.build_graph --tenant hotpot --workers 2 --no-resolve --out-dir results/p3_noresolve
	python -m scripts.run_p3_eval --tenant hotpot --arms vector,hybrid --out-dir results/p3_noresolve
	python -m scripts.build_graph --tenant hotpot --workers 2
p3-eval:        ; python -m scripts.run_p3_eval --tenant hotpot --arms vector,vector_rerank,hybrid,hybrid_ngram --single data/hotpot/questions_single.jsonl --only-validated
p3: export SYSTEM = project3
p3:             ; uvicorn app.api.main:app --reload --port 8000
test-neo4j:     ; python -c "import os, pytest; os.environ.setdefault('NEO4J_URI', 'bolt://localhost:7687'); raise SystemExit(pytest.main(['-q', 'tests/integration/test_graph_store_contract.py']))"
