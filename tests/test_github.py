from ase.github import render_pr_body


def test_pr_body_is_explicitly_draft() -> None:
    body = render_pr_body("run_1", "Repair parser", [("tests", True)])
    assert "| tests | Passed |" in body
    assert "human reviewer" in body
    assert "run_1" in body
