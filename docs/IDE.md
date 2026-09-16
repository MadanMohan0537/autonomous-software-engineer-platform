# Governed IDE

## Purpose

The IDE makes the agent's evidence and authority boundaries visible. It is not intended
to imitate a desktop editor feature-for-feature. Its primary job is to let a reviewer
understand a repository, inspect an agent run, approve a bounded plan, and evaluate the
result without leaving the control plane.

## Surfaces

| Surface | Capability |
|---|---|
| Activity rail | Explorer, search, runs, and policy views |
| Repository explorer | Directory navigation and safe text-file preview |
| Search | Case-insensitive repository search with file and line navigation |
| Editor region | Read-only source preview and working-tree diff |
| Governed terminal | Named tests, lint, and type-check recipes only |
| Agent inspector | Issue, retrieved context, relevance, plan, and approvals |
| Bottom panel | Command output, event timeline, checks, and problems |
| Status bar | Branch context, active run, encoding, and policy status |

## Security model

The IDE never trusts a browser-provided filesystem path. `IDEWorkspace` resolves every
path against `ASE_REPOSITORY_ROOT`, rejects traversal, excludes sensitive or generated
directories, limits preview size, and rejects binary files. Diff requests use argument
arrays rather than a shell. Search ignores excluded directories and large files.

The terminal is intentionally recipe-based. The browser sends a recipe name such as
`tests`; the server maps it to a version-controlled argument array and then applies the
existing command policy. User-provided shell text is never evaluated.

## API

| Method | Route | Use |
|---|---|---|
| `GET` | `/api/ide/tree?path=` | List one directory |
| `GET` | `/api/ide/file?path=` | Preview a text file |
| `GET` | `/api/ide/search?query=` | Search repository text |
| `GET` | `/api/ide/diff?path=` | Inspect working-tree changes |
| `POST` | `/api/ide/run` | Execute a named verification recipe |

Existing `/api/runs` endpoints power issue creation, analysis, run inspection, feedback,
and plan decisions.

## Accessibility and responsive behavior

- All primary controls are native buttons, inputs, forms, or dialogs.
- Icon-only controls include accessible labels.
- Status and toast regions announce updates.
- The workspace has a mobile layout that preserves the editor and bottom panel.
- Light and dark palettes use shared semantic variables.
- Reduced-motion preferences disable interface transitions.

## Deliberate boundaries

Version 0.2 provides read-only file inspection. Direct browser file editing remains
disabled until the platform can bind edits to an approved plan, calculate a content
digest, display the exact patch, and require a second approval before applying it.
Arbitrary terminals, autonomous merges, and production deployment remain denied.

