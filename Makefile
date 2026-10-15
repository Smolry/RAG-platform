.PHONY: p1 p2 tune-cache eval-router paraphrases bench dashboard up install corpus ingest-baseline api eval-baseline tenants ingest-tenants test security measure-p2
ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
.SHELLFLAGS := /c
SET_SYSTEM_BASELINE = set "SYSTEM=baseline" &&
SET_SYSTEM_PROJECT2 = set "SYSTEM=project2" &&
SET_SYSTEM_PROJECT1 = set "SYSTEM=project1" &&
else
SET_SYSTEM_BASELINE = SYSTEM=baseline
SET_SYSTEM_PROJECT2 = SYSTEM=project2
SET_SYSTEM_PROJECT1 = SYSTEM=project1
endif

install:        ; python -m pip install -r requirements.txt
up:             ; docker compose up -d
corpus:         ; python -m scripts.fetch_corpus
ingest-baseline:; python -m scripts.ingest --path data/raw --tenant acme --classification internal --roles employee,manager,hr,admin
api:            ; $(SET_SYSTEM_BASELINE) python -m uvicorn app.api.main:app --reload --port 8000
eval-baseline:  ; python -m scripts.run_evaluation --system baseline --only-validated
tenants:        ; python -m scripts.generate_tenant_corpus
ingest-tenants: ; python -m scripts.ingest --manifest data/synthetic/manifest.json
test:           ; python -m pytest -q
security:       ; python -m pytest -q tests/security && python -m scripts.run_security_suite
measure-p2:     ; python -m scripts.measure_project2
p1:             ; $(SET_SYSTEM_PROJECT1) python -m uvicorn app.api.main:app --reload --port 8000
p2:             ; $(SET_SYSTEM_PROJECT2) python -m uvicorn app.api.main:app --reload --port 8000
tune-cache:     ; python -m scripts.tune_cache_threshold
eval-router:    ; python -m scripts.eval_router --split dev
paraphrases:    ; python -m scripts.make_paraphrases
bench:          ; python -m scripts.run_benchmark --tenant acme --user eval@acme --roles employee --only-validated
dashboard:      ; python -m streamlit run dashboard/streamlit_app.py
