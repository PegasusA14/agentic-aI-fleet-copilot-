.PHONY: setup load-data test lint golden contracts fixtures frontend-install frontend-dev frontend-build frontend-test

setup:
	cd backend && uv sync

load-data:
	cd backend && uv run python -m fleet_copilot.data.cli

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check src tests
	cd backend && uv run ruff format --check src tests

golden:
	python3 evals/golden/oracle.py

contracts:
	cd backend && uv run python -m fleet_copilot.contracts_export

fixtures:
	cd backend && uv run python -m fleet_copilot.agent.export_fixtures

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-build:
	cd frontend && npm run build

frontend-test:
	cd frontend && npm run typecheck && npm test
