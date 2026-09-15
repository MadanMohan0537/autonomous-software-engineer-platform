# Operations

## Runtime processes

The deployment has two process roles:

- `uvicorn ase.api:app`: HTTP API, webhook receiver, and review console.
- `ase-worker`: claims durable tasks and runs repository analysis.

Both roles share the SQLite database in the single-node reference deployment. A
multi-replica production deployment should replace SQLite with a transactional
PostgreSQL implementation of `RunStore` and the task queue.

## Configuration

Copy `.env.example` into the deployment secret manager. Do not commit populated
values. `GITHUB_WEBHOOK_SECRET`, `GITHUB_TOKEN`, and `ASE_MODEL_API_KEY` are secrets.

Production startup should call `Settings.validate_production()` and refuse to start
when the local execution backend is selected, sandbox networking is enabled by
default, or webhook authentication is missing.

## GitHub webhook

Configure an Issues webhook targeting `/api/github/webhook` and use the same random
secret as `GITHUB_WEBHOOK_SECRET`. The receiver:

1. Verifies `X-Hub-Signature-256` before decoding the payload.
2. Deduplicates `X-GitHub-Delivery` values.
3. Accepts only `issues` events.
4. Enqueues only opened or labeled issues carrying `ase:ready`.

The worker assumes the installed repository is already present below
`ASE_REPOSITORY_ROOT`. Repository cloning is intentionally separate because credential
scope and clone policy differ across installations.

## Data and recovery

Persist the directory containing `ASE_DATABASE_PATH` and `ASE_FEEDBACK_PATH`. SQLite
uses WAL mode. Back up the main database, WAL, and shared-memory files as one unit, or
use SQLite's online backup API. Restore into a stopped deployment and run migrations
before restarting workers.

## Isolation

The local runner is a development adapter, not a security boundary. For untrusted code,
the `ASE_EXECUTION_BACKEND` must point to a separate container or microVM service that
provides:

- an ephemeral filesystem;
- no host Docker socket;
- default-deny network policy;
- syscall, process, CPU, memory, and wall-time limits;
- a reduced environment without control-plane credentials;
- immutable execution artifacts returned to the control plane.

## Observability

The package initializes OpenTelemetry using `configure_telemetry`. Deployments should
attach an OTLP exporter and collect traces, structured logs, queue depth, task age,
verification duration, policy denials, and webhook-rejection counts. Raw source,
prompts, patches, and command output require explicit data-handling policies before
export.

## Release checks

Every release must pass:

```bash
ruff check .
ruff format --check .
mypy src
pytest --cov=ase --cov-fail-under=85
docker build .
```

