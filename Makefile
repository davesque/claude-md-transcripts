.PHONY: install
install:
	uv sync --all-extras

.PHONY: test
test:
	uv run pytest

.PHONY: cov
cov:
	uv run pytest --cov

.PHONY: lint
lint:
	uv run ruff check .

.PHONY: fmt
fmt:
	uv run ruff format .

.PHONY: typecheck
typecheck:
	uv run ty check claude_md_transcripts

.PHONY: check
check: lint typecheck test

.PHONY: clean
clean:
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
