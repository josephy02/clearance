.PHONY: install test lint data eval serve

install:
	uv sync

test:
	uv run pytest -q

lint:
	uv run ruff check .

data:
	uv run python -m clearance.synth

eval:
	uv run python -m clearance.eval

serve:
	uv run uvicorn clearance.api.app:app --reload --port 8000
