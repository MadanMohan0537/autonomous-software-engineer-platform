.PHONY: install check test run index

install:
	python -m pip install -e '.[dev]'

check:
	ruff check .
	mypy src
	pytest --cov=ase --cov-report=term-missing --cov-fail-under=85

test:
	pytest

run:
	uvicorn ase.api:app --reload

index:
	ase index . --output artifacts/index.json

