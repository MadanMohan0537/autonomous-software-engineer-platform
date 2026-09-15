import pytest

from ase.mutation import MutationRunner


def test_parses_mutation_report() -> None:
    report = MutationRunner.parse('{"killed": 8, "survived": 2}')
    assert report.score == 0.8


def test_invalid_mutation_json_fails() -> None:
    with pytest.raises(ValueError):
        MutationRunner.parse("not-json")
