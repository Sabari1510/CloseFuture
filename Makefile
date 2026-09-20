.PHONY: setup db-up migrate ingest test acceptance sweep trace cost serve check-setup

setup:
	pip install -e ".[dev]" 2>NUL || pip install -e .

db-up:
	docker run -d --name pgtest -e POSTGRES_PASSWORD=postgres -p 5433:5432 pgvector/pgvector:pg16

migrate:
	python scripts/migrate.py

ingest:
	python scripts/ingest.py

test:
	pytest tests/unit tests/integration -q

acceptance:
	python scripts/budget_gate.py && pytest tests/acceptance -q

sweep:
	python -m src.app.jobs.sweep

trace:
	python scripts/export_trace.py --session $(SESSION)

cost:
	python scripts/cost_report.py

serve:
	uvicorn src.app.api.main:app --port 8000

check-setup:
	python scripts/check_setup.py
