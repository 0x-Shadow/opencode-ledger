PY ?= python3
SMOKE_PORT ?= 8799

.PHONY: lint test smoke serve demo docker

lint:            ## static checks (needs: pip install ruff)
	ruff check .

test:            ## run the stdlib-only regression suite
	$(PY) -m unittest discover -s tests -v

smoke:           ## boot the demo server and check every route answers 200
	@set -eu; \
	$(PY) -m opencode_ledger demo --port $(SMOKE_PORT) >/dev/null 2>&1 & pid=$$!; \
	trap 'kill $$pid 2>/dev/null || true' EXIT; \
	ready=""; \
	for i in $$(seq 1 30); do \
	  if curl -fsS -o /dev/null "http://127.0.0.1:$(SMOKE_PORT)/api/healthz" 2>/dev/null; then ready=1; break; fi; \
	  sleep 1; \
	done; \
	[ -n "$$ready" ] || { echo "smoke: server did not start"; exit 1; }; \
	for url in / /stats.json /api/summary /api/healthz /api/ready /api/export.csv /metrics; do \
	  code=$$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$(SMOKE_PORT)$$url"); \
	  if [ "$$code" != 200 ]; then echo "smoke: FAIL $$url -> $$code"; exit 1; fi; \
	  echo "smoke: ok $$url"; \
	done; \
	echo "smoke: all routes healthy"

serve:           ## run the real dashboard on http://localhost:8765/
	$(PY) serve.py

demo:            ## run the dashboard with synthetic data, no database
	$(PY) -m opencode_ledger demo

docker:          ## build and start via Docker Compose
	docker compose up -d --build
