.PHONY: install check lint type test smoke run worker index knowledge sandbox-image evals mutate

PY ?= python
REPO ?= .

install:
	$(PY) -m pip install -e '.[dev,index,graph,sandbox]'

lint:
	ruff check .
	ruff format --check .

type:
	mypy src

test:
	pytest --cov=ase --cov-report=term-missing --cov-fail-under=85

check: lint type test

# the same no-network, no-credential sequence CI runs after the unit tests
smoke:
	ase index $(REPO) --output artifacts/index.json
	ase knowledge build $(REPO)
	ase knowledge search $(REPO) "how are pull request reviews mirrored" -k 3
	ase knowledge eval $(REPO) --limit 5
	ase mutate $(REPO) --paths src/ase/policy.py --tests tests/test_policy.py --budget 120

run:
	uvicorn ase.api:app --reload

worker:
	ase-worker

index:
	ase index $(REPO) --output artifacts/index.json

knowledge:
	ase knowledge build $(REPO)

# execution sandbox used when ASE_EXECUTION_BACKEND=docker
sandbox-image:
	docker build -f docker/sandbox.Dockerfile -t ase-sandbox:latest .

# harvest a suite from this repository's history, then run and report it
evals:
	ase eval harvest $(REPO) --limit 10 --output evals/suites/local.json
	ase eval run $(REPO) --suite evals/suites/local.json --results-dir evals/results
	ase eval report --results-dir evals/results --cases

mutate:
	ase mutate $(REPO) --paths $(PATHS) --budget 600
