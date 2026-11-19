.PHONY: setup load-data test lint golden contracts

setup:
	cd backend && uv sync

load-data:
	cd backend && uv run python -m fleet_copilot.data.cli

test:
	cd backend && uv run pytest

lint:
	cd backend && uv run ruff check src tests

golden:
	python3 evals/golden/oracle.py

contracts:
	cd backend && uv run python -m fleet_copilot.contracts_export
