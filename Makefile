.PHONY: install-dev lint test-unit test-e2e test build

install-dev:
	uv venv && uv pip install -e ".[dev]" && pre-commit install

lint:
	pre-commit run --all-files

test-unit:
	pytest tests/ -v --ignore=tests/test_e2e.py

test-e2e:
	pytest tests/test_e2e.py -v

test: lint test-unit

build:
	uv build
