"""The benchmark suite: cases and deterministic graders. Pure functions, unit tested."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SUITE_PATH = Path(__file__).with_name("suite.yaml")
_NUMBER = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    task: str
    difficulty: int
    grader: str
    expected: Any
    prompt: str
    choices: tuple[str, ...] = ()
    tolerance: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def band(self) -> str:
        return "high" if self.difficulty >= 4 else "low"

    def messages(self) -> list[dict[str, str]]:
        return [{"role": "user", "content": self.prompt}]

    def oracle(self) -> dict[str, Any]:
        """Annotation for SIMULATED endpoints only: lets the mock model answer correctly with
        a probability set by its skill and this case's difficulty. Never sent to a real
        provider (the gateway strips benchmark_* metadata for non-simulated endpoints)."""
        expected = json.dumps(self.expected) if self.grader == "json" else self.expected
        if self.grader == "contains":
            expected = " ".join(self.expected)
        elif self.grader == "regex":
            expected = self.expected.replace("\\.", ".")
        return {
            "benchmark_case": self.id,
            "benchmark_task": self.task,
            "benchmark_difficulty": str(self.difficulty),
            "benchmark_expected": str(expected),
        }


@dataclass(frozen=True, slots=True)
class Suite:
    version: str
    cases: tuple[Case, ...]


@lru_cache(maxsize=4)
def load_suite(path: str | None = None) -> Suite:
    raw = yaml.safe_load(Path(path or SUITE_PATH).read_text())
    cases = tuple(
        Case(
            id=c["id"],
            task=c["task"],
            difficulty=int(c["difficulty"]),
            grader=c["grader"],
            expected=c["expected"],
            prompt=c["prompt"],
            choices=tuple(c.get("choices") or ()),
            tolerance=float(c.get("tolerance", 0.0)),
        )
        for c in raw["cases"]
    )
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids in the benchmark suite")
    for c in cases:
        textual = c.expected if c.grader == "contains" else [c.expected]
        if c.grader in ("exact", "choice", "contains", "regex") and not all(
            isinstance(x, str) for x in textual
        ):
            # YAML reads bare yes/no/on/off as booleans; the author must quote them.
            raise ValueError(f"case {c.id}: expected must be text (quote yes/no values)")
    return Suite(str(raw["version"]), cases)


def _norm(s: str) -> str:
    return re.sub(r"[\s.!?]+$", "", s.strip().lower())


def _json_from(answer: str) -> Any:
    m = _FENCE.search(answer)
    text = m.group(1) if m else answer
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    return json.loads(text[start : end + 1])


def grade(case: Case, answer: str | None) -> bool:
    if not answer:
        return False
    a = answer.strip()
    low = a.lower()
    if case.grader == "exact":
        return _norm(a) == _norm(str(case.expected))
    if case.grader == "choice":
        exp = str(case.expected).lower()
        others = [c.lower() for c in case.choices if c.lower() != exp]
        words = set(re.findall(r"[a-z-]+", low))
        return exp in words and not any(o in words for o in others)
    if case.grader == "number":
        m = _NUMBER.search(a)
        if not m:
            return False
        value = float(m.group(0).replace(",", ""))
        return math.isclose(value, float(case.expected), abs_tol=case.tolerance or 1e-9)
    if case.grader == "contains":
        # Whole words or phrases: "no" must not match inside "not" or "one".
        return all(
            re.search(rf"(?<!\w){re.escape(str(e).lower())}(?!\w)", low) for e in case.expected
        )
    if case.grader == "regex":
        return re.search(str(case.expected), a, re.IGNORECASE) is not None
    if case.grader == "json":
        try:
            data = _json_from(a)
        except (ValueError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and all(data.get(k) == v for k, v in case.expected.items())
    raise ValueError(f"unknown grader {case.grader}")
