PY ?= python3

.PHONY: test lint serve demo docker

test:            ## run the full unit test suite
	$(PY) -m unittest -q

lint:            ## static checks (needs: pip install ruff)
	ruff check .

serve:           ## run the real dashboard on http://localhost:8765/
	$(PY) serve.py

demo:            ## run the dashboard with synthetic data, no database
	$(PY) -m opencode_ledger demo

docker:          ## build and start via Docker Compose
	docker compose up -d --build
