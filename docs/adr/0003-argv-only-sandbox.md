# ADR 0003: sandboxes accept argv arrays only, never a shell string

**Status:** accepted

## Context

The agent asks to run commands. A shell string is the easiest thing to pass through and
the hardest thing to police: `pytest; curl attacker`, backticks, redirections and
environment expansions all hide inside one string.

## Decision

`ase.sandbox.Sandbox.run` takes `argv: list[str]`. The policy engine checks the
executable against an allowlist and `git` subcommands against a denylist before anything
runs; there is no `shell=True` anywhere in the package. `LocalSandbox` is the development
runner and is documented as *not* a security boundary. `DockerSandbox` adds `--network
none`, CPU, memory and PID limits, `--cap-drop ALL`, `no-new-privileges` and an
unprivileged user, with only the worktree bind-mounted.

## Consequences

- A whole class of injection is impossible by construction, not by filtering.
- Some legitimate commands are awkward (`pytest -k "a or b"` works; pipes do not).
- The allowlist lives in `.ase/policy.yaml` per repository and fails closed.
