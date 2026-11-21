.PHONY: setup load-data api ask doctor live-check test lint golden contracts fixtures frontend-install frontend-dev frontend-build frontend-test dev demo

setup:
	cd backend && uv sync

load-data:
	cd backend && uv run python -m fleet_copilot.data.cli

api:
	cd backend && uv run python -m fleet_copilot.api

ask:
	cd backend && uv run python -m fleet_copilot.cli ask "$(Q)" --trace

doctor:
	cd backend && uv run python -m fleet_copilot.cli doctor --live

live-check:
	cd backend && FLEET_MODEL_MODE=live uv run python -m fleet_copilot.live_check --cassette ../var/cassettes/live-check.jsonl

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

dev:
	sh scripts/dev.sh

demo: frontend-build
	$(MAKE) api
