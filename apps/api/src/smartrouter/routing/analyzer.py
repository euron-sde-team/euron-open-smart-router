"""Request analysis: what kind of task is this, how hard is it, what does it need?

Deterministic and explainable by design. Every conclusion carries the signals that produced
it, so a decision in the log can be read and challenged ("classified as coding because of a
code fence and the word 'refactor'"). A learned classifier can replace this later behind
the same `Analysis` contract; the router itself does not care how the task was detected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from smartrouter.gateway.tokens import estimate_chat_input

TASKS = (
    "coding",
    "math",
    "reasoning",
    "extraction",
    "classification",
    "summarization",
    "translation",
    "chat",
)
# Expected output size per task when the caller gives no max_tokens: used for cost estimates.
DEFAULT_OUTPUT = {
    "coding": 700,
    "math": 300,
    "reasoning": 600,
    "extraction": 200,
    "classification": 20,
    "summarization": 250,
    "translation": 300,
    "chat": 300,
}
# Base difficulty before signals: classification is usually easy, reasoning rarely is.
BASE_COMPLEXITY = {
    "coding": 3,
    "math": 3,
    "reasoning": 3,
    "extraction": 2,
    "classification": 1,
    "summarization": 2,
    "translation": 2,
    "chat": 2,
}

_RULES: dict[str, list[tuple[str, str]]] = {
    # (regex, signal name). Weighted by count of distinct signals that fire.
    "coding": [
        (r"```", "code fence"),
        # Code keywords count only on a code-shaped line: lowercase at the line start, so
        # English like "Return only the address" or "the class of bug" does not match.
        (
            r"(?m)(?-i:^\s*(def|class|function|const|let|var|import|return|public static)\s+\S)",
            "code keyword",
        ),
        (r"\b(python|javascript|typescript|golang|rust|java|sql|bash|regex)\b", "language name"),
        (r"\b(refactor|debug|stack ?trace|unit tests?|compile|bug|endpoint|api)\b", "code verb"),
        (
            r"\b(write|implement|fix) (a |an |the )?(function|script|program|query|class)\b",
            "write code",
        ),
    ],
    "math": [
        (
            r"\b(solve|calculate|compute|integral|derivative|equation|probability|percent)\b",
            "math verb",
        ),
        (r"\d+\s*[\+\-\*/\^x%]\s*\d+", "arithmetic"),
        (r"\b(how many (ways|combinations|permutations)|choose \d+ from)\b", "counting"),
        (r"\b(rounded to|number only|decimal only|to \d+ decimal places)\b", "numeric answer"),
        (r"\b(prove|theorem|lemma)\b", "proof"),
    ],
    "reasoning": [
        (
            r"\b(why|explain why|trade-?offs?|pros and cons|compare|evaluate|justify)\b",
            "reasoning cue",
        ),
        (r"\b(step by step|think through|reason about|analy[sz]e)\b", "deliberation"),
        (r"\b(strategy|plan|design|architecture|decide|recommend)\b", "planning"),
    ],
    "extraction": [
        (r"\b(extract|parse|pull out|fields?|structured|entities)\b", "extract verb"),
        (r"\b(json|yaml|csv|schema)\b", "structured output"),
    ],
    "classification": [
        (
            r"\b(classify|categori[sz]e|label|sentiment|tag|which category|is this (spam|positive|negative))\b",
            "classify verb",
        ),
        (r"\b(one of|choose from|one word|single word|yes or no)\b", "closed answer"),
    ],
    "summarization": [
        (
            r"\b(summari[sz]e|summary|tl;?dr|key points|condense|recap|bullet points)\b",
            "summarize verb",
        ),
    ],
    "translation": [
        (r"\btranslat(e|ion)\b", "translate verb"),
        (
            r"\b(into|to|in) (french|spanish|german|hindi|japanese|chinese|arabic|portuguese|italian)\b",
            "target language",
        ),
    ],
}
_COMPILED = {
    t: [(re.compile(p, re.IGNORECASE), name) for p, name in rules] for t, rules in _RULES.items()
}

_HARD = re.compile(
    r"\b(optimi[sz]e|edge cases?|concurren\w*|distributed|race condition|prove|formal|"
    r"security|scal(e|ability)|production[- ]ready|multi-?step|complex|rigorous|"
    # legal and contractual language
    r"notwithstanding|clauses?|liab(le|ility)|indemn\w*|contract(ual)?|"
    # nuance, tone and idiom
    r"sarcas\w*|iron(y|ic)|idioms?|nuanc\w*|diplomatic|implicit|not word for word|"
    # analysis of causes, risks and trade-offs
    r"main driver|root cause|risks?|conflicts?|least risk|"
    # algorithms and probability
    r"probability|idempoten\w*|O\([^)]{1,12}\))",
    re.IGNORECASE,
)
# Wording that says the task itself is easy. Answer-format phrases ("one word", "yes or no",
# "in one sentence") are deliberately absent: a one-word verdict can still be hard to reach.
_EASY = re.compile(
    r"\b(briefly|simple|quick question|in simple words|for a child)\b",
    re.IGNORECASE,
)
_REQUIREMENT_LINE = re.compile(r"^\s*(\d+[.)]|[-*])\s+\S", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Analysis:
    task: str
    complexity: int  # 1 (trivial) to 5 (hard)
    signals: tuple[str, ...]
    input_tokens: int
    output_tokens: int  # the allowance used for cost estimates
    needs_tools: bool = False
    needs_vision: bool = False
    needs_json: bool = False
    overridden: tuple[str, ...] = field(default=())

    @property
    def band(self) -> str:
        return "high" if self.complexity >= 4 else "low"


def _text(messages: list[dict[str, Any]], roles: tuple[str, ...]) -> str:
    parts: list[str] = []
    for m in messages:
        if m.get("role") not in roles:
            continue
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(p.get("text", "") for p in content if isinstance(p, dict))
    return "\n".join(parts)


def _has_image(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = m.get("content")
        if isinstance(content, list) and any(
            isinstance(p, dict) and p.get("type") in ("image_url", "input_image", "image")
            for p in content
        ):
            return True
    return False


def analyze(
    body: dict[str, Any],
    tokenizer: str = "o200k_base",
    *,
    task_override: str | None = None,
    complexity_override: int | None = None,
) -> Analysis:
    messages = body.get("messages") or []
    user_text = _text(messages, ("user",))
    all_text = _text(messages, ("system", "user"))
    input_tokens = estimate_chat_input(body, tokenizer)

    # Task: the rule set with the most distinct signals in the user's words wins; the system
    # prompt counts at half weight (it often describes the app, not the request).
    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    system_text = _text(messages, ("system",))
    # The instruction usually sits in the first line; material to work on follows it.
    first_line = user_text.strip().split("\n", 1)[0]
    first_at: dict[str, int] = {}
    for task, rules in _COMPILED.items():
        for rx, name in rules:
            if m := rx.search(user_text):
                bonus = 0.5 if rx.search(first_line) else 0.0
                scores[task] = scores.get(task, 0) + 1.0 + bonus
                hits.setdefault(task, []).append(name)
                first_at[task] = min(first_at.get(task, m.start()), m.start())
            elif system_text and rx.search(system_text):
                scores[task] = scores.get(task, 0) + 0.5
                hits.setdefault(task, []).append(f"{name} (system)")
    fmt = body.get("response_format")
    needs_json = isinstance(fmt, dict) and fmt.get("type") in ("json_object", "json_schema")
    if needs_json:
        scores["extraction"] = scores.get("extraction", 0) + 1.0
        hits.setdefault("extraction", []).append("JSON response format")

    if scores:
        # Ties go to the task whose cue comes first (the instruction usually leads: "Summarize
        # this ... API" is a summary), then to the task listed first in TASKS.
        task = max(
            scores,
            key=lambda t: (scores[t], -first_at.get(t, len(user_text) + 1), -TASKS.index(t)),
        )
        signals = [f"{task}: {s}" for s in hits[task]]
    else:
        task, signals = "chat", ["no task signals: general chat"]

    overridden: list[str] = []
    if task_override in TASKS:
        task = str(task_override)
        signals.append(f"task set by caller: {task}")
        overridden.append("task")

    # Complexity: base for the task, moved by size, explicit requirements and wording.
    complexity = BASE_COMPLEXITY[task]
    if input_tokens > 6000:
        complexity += 2
        signals.append(f"long input (~{input_tokens} tokens)")
    elif input_tokens > 1500:
        complexity += 1
        signals.append(f"sizeable input (~{input_tokens} tokens)")
    requirements = len(_REQUIREMENT_LINE.findall(user_text))
    if requirements >= 4:
        complexity += 1
        signals.append(f"{requirements} listed requirements")
    hard = sorted({m.group(0).lower() for m in _HARD.finditer(all_text)})
    if hard:
        complexity += 1 if len(hard) < 2 else 2
        signals.append("hard cues: " + ", ".join(hard[:4]))
    if task == "reasoning" and len(hits.get("reasoning", [])) >= 2:
        complexity += 1
        signals.append("several reasoning cues")
    if _EASY.search(user_text):
        complexity -= 1
        signals.append("asks for a short or simple answer")
    if body.get("tools"):
        complexity += 1
        signals.append("tool use")
    complexity = max(1, min(5, complexity))
    if complexity_override is not None and 1 <= complexity_override <= 5:
        complexity = complexity_override
        signals.append(f"complexity set by caller: {complexity}")
        overridden.append("complexity")

    requested_out = body.get("max_completion_tokens") or body.get("max_tokens")
    output_tokens = int(requested_out) if requested_out else DEFAULT_OUTPUT[task]
    return Analysis(
        task=task,
        complexity=complexity,
        signals=tuple(signals),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        needs_tools=bool(body.get("tools")),
        needs_vision=_has_image(messages),
        needs_json=needs_json,
        overridden=tuple(overridden),
    )
