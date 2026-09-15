# Threat Model

## Protected assets

- Source code and repository history
- GitHub credentials and installation tokens
- CI and deployment credentials
- Developer secrets and environment variables
- Integrity of tests and quality gates
- Review decisions and audit events

## Principal threats

| Threat | Example | Required control |
|---|---|---|
| Prompt injection | Repository comment asks agent to reveal secrets | Treat content as data; no secret access in planner |
| Command injection | Generated shell command includes a pipeline | Argument arrays and executable allowlist |
| Workspace escape | Command writes outside checkout | Resolved-path containment and OS isolation |
| Test weakening | Patch adds skips or removes assertions | Diff integrity checks and hidden tests |
| Credential exfiltration | Test sends environment data over HTTP | Network denied and reduced environment |
| Excessive patch | Agent refactors unrelated modules | Changed-file limit and plan-to-diff comparison |
| Confused deputy | Agent uses GitHub token for unintended repo | Installation-scoped token and repository binding |
| Replay | Webhook is delivered twice | Delivery-id idempotency in production adapter |
| Supply chain | Patch adds malicious dependency | Dependency approval and provenance checks |

## Known first-release limitations

`LocalSandbox` constrains commands and paths but is not an OS security boundary. It is
appropriate for trusted development fixtures only. Untrusted repositories require a
container or microVM with filesystem, network, syscall, process, and resource controls.

The policy YAML documents intended repository policy; version 0.1 uses equivalent
typed defaults in code. Loading and validating policy files is a planned extension.

## Security invariants

- Models never receive merge or production-deployment credentials.
- Verification cannot grant authority beyond the policy engine.
- A failed or missing check cannot be interpreted as a pass.
- Logs must redact secrets before persistence.
- Human approval is attached to the exact plan or patch digest it approves.

