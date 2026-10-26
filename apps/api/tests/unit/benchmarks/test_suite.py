from pathlib import Path

import pytest

from smartrouter.benchmarks.suite import Case, grade, load_suite
from smartrouter.routing.analyzer import TASKS


def _case(grader: str, expected: object, **kw: object) -> Case:
    return Case(
        id="t", task="chat", difficulty=1, grader=grader, expected=expected, prompt="p", **kw
    )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("case", "answer", "ok"),
    [
        (_case("exact", "Paris"), "paris.", True),
        (_case("exact", "Paris"), "Paris, France", False),
        (_case("choice", "positive", choices=("positive", "negative")), "Positive", True),
        (
            _case("choice", "positive", choices=("positive", "negative")),
            "positive or negative",
            False,
        ),
        (_case("number", 0.25), "The answer is 0.25 exactly", True),
        (_case("number", 1200), "1,200", True),
        (_case("number", 3.14, tolerance=0.01), "3.141", True),
        (_case("number", 252), "no number here", False),
        (_case("contains", ["refund", "approved"]), "Refund was APPROVED.", True),
        (_case("contains", ["refund", "approved"]), "refund pending", False),
        (_case("regex", r"\bA-\d{4}\b"), "order A-7741", True),
        (
            _case("json", {"name": "Sam", "amount": 120}),
            '```json\n{"name": "Sam", "amount": 120}\n```',
            True,
        ),
        (_case("json", {"name": "Sam", "amount": 120}), '{"name": "Sam", "amount": "120"}', False),
        (_case("json", {"a": 1}), "not json at all", False),
        (_case("json", {"a": 1}), "{broken", False),
        (_case("exact", "x"), "", False),
    ],
)
def test_graders(case: Case, answer: str, ok: bool) -> None:
    assert grade(case, answer) is ok


def test_unknown_grader_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown grader"):
        grade(_case("vibes", "x"), "x")


def test_the_shipped_suite_is_valid_and_balanced() -> None:
    suite = load_suite()
    assert {c.task for c in suite.cases} == set(TASKS)
    assert len({c.id for c in suite.cases}) == len(suite.cases)
    for c in suite.cases:
        assert 1 <= c.difficulty <= 5
        assert c.band == ("high" if c.difficulty >= 4 else "low")


def test_every_case_passes_its_own_oracle_answer() -> None:
    for c in load_suite().cases:
        assert grade(c, c.oracle()["benchmark_expected"]), c.id
        assert not grade(c, "I am not certain about this one."), c.id


def test_yaml_booleans_are_rejected(tmp_path: Path) -> None:
    p = tmp_path / "suite.yaml"
    p.write_text(
        'version: "1"\ncases:\n  - {id: a, task: chat, difficulty: 1, grader: contains, expected: [no], prompt: "x"}\n'
    )
    with pytest.raises(ValueError, match="quote"):
        load_suite(str(p))


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    row = '  - {id: a, task: chat, difficulty: 1, grader: exact, expected: "x", prompt: "x"}\n'
    p = tmp_path / "suite.yaml"
    p.write_text('version: "1"\ncases:\n' + row + row)
    with pytest.raises(ValueError, match="duplicate"):
        load_suite(str(p))
