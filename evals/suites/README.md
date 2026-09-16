# Evaluation suites

A suite is a JSON file with a fixed list of tasks and the tests that define success for
each one. Suites are committed so that any number in a report can be traced back to the
exact tasks that produced it. A task is *resolved* when its `fail_to_pass` tests pass and
its `pass_to_pass` tests still pass after the agent's patch — the SWE-bench definition.

```json
{
  "name": "local-history",
  "description": "bug-fix commits harvested from this repository",
  "tasks": [
    {
      "id": "98dcabb691",
      "repository": ".",
      "base_sha": "c45a2067a3...",
      "title": "fix: ...",
      "body": "...",
      "fail_to_pass": ["tests/test_evals.py::test_grades_fail_to_pass"],
      "pass_to_pass": ["tests/test_policy.py"],
      "test_patch": {"tests/test_evals.py": "..."},
      "expected_files": ["src/ase/evals/grade.py"]
    }
  ]
}
```

When `test_patch` is present the acceptance tests are hidden from the agent, exactly as
SWE-bench hides them: the agent sees only the issue text and must build its own
reproduction; the hidden tests are applied on a clean checkout at grading time.

## Where suites come from

`ase eval harvest <repo> --limit N --output evals/suites/<name>.json` walks the repository's
history for commits that change both source and test files, and turns each into a task
whose `base_sha` is the parent commit and whose tests are the ones the commit touched.
The suite for this repository is produced by `make evals` and is not committed — it
changes with every commit.

SWE-bench instances (`princeton-nlp/SWE-bench_Lite` JSON/JSONL exports) load through
`ase.evals.swebench.load_instances`. The platform produces the predictions file; the
*official* harness (`python -m swebench.harness.run_evaluation`, Docker required) does
the grading. No SWE-bench number is reported from this repository's own grader.

## Results

`ase eval run` writes one JSON result per task to `evals/results/` (git-ignored except for
the `.gitkeep`), and `ase eval report --cases` renders resolve rate, cost and token
comparison tables across run configurations. Results are also recorded in the trace
store (`eval_results` table) so they can be joined with reviews and feedback labels.
