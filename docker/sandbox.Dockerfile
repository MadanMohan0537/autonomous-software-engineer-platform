# Execution sandbox for agent runs (ASE_EXECUTION_BACKEND=docker).
#
# The control plane bind-mounts one git worktree at /workspace and runs allow-listed
# commands here with: --network none --cpus --memory --pids-limit
# --security-opt no-new-privileges --cap-drop ALL --user 10001:10001 (see
# `ase.sandbox.DockerSandbox`). Nothing in this image is trusted with credentials;
# it only needs the toolchain the target repository's tests require.
#
#   docker build -f docker/sandbox.Dockerfile -t ase-sandbox:latest .

FROM python:3.12-slim

ARG PIP_NO_CACHE_DIR=1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

# Test tooling used by the verification, coverage and mutation-testing steps.
# Pin these to what `pyproject.toml` allows under the `sandbox` extra.
RUN pip install \
    "pytest>=8.3,<10" \
    "coverage>=7,<8" \
    "hypothesis>=6,<7"

# Unprivileged user matching the UID the control plane passes with --user.
RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

USER sandbox
WORKDIR /workspace
ENTRYPOINT []
CMD ["python", "--version"]
