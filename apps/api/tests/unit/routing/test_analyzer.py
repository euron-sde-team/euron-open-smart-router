import pytest

from smartrouter.routing.analyzer import TASKS, analyze


def _a(text: str, **body: object):  # type: ignore[no-untyped-def]
    return analyze({"messages": [{"role": "user", "content": text}], **body})


@pytest.mark.parametrize(
    ("prompt", "task"),
    [
        ("Review this function:\n```python\ndef f(x):\n    return x\n```", "coding"),
        ("Solve 3x + 7 = 22", "math"),
        ("How many ways can 3 people be seated in a row? Number only.", "math"),
        ("Explain why the trade-offs favour a read replica", "reasoning"),
        ("Extract the email as JSON", "extraction"),
        ("Classify the sentiment as positive or negative", "classification"),
        ("Summarize this thread in key points", "summarization"),
        ("Translate into French: good morning", "translation"),
        ("Hello there, how are you?", "chat"),
    ],
)
def test_detects_the_task(prompt: str, task: str) -> None:
    assert _a(prompt).task == task


def test_english_return_is_not_code() -> None:
    a = _a("Extract the email from this text. Return only the address.")
    assert a.task == "extraction"
    assert not any("code keyword" in s for s in a.signals)


def test_code_keyword_on_a_code_line_counts() -> None:
    a = _a("what does this do?\nimport os\nreturn os.getcwd()")
    assert any("code keyword" in s for s in a.signals)


def test_the_instruction_line_breaks_ties() -> None:
    # "percent" is a math cue, but the instruction asks for a summary.
    a = _a("Summarize the decision.\n\nThe board cut marketing by ten percent.")
    assert a.task == "summarization"


def test_system_prompt_counts_at_half_weight() -> None:
    body = {
        "messages": [
            {"role": "system", "content": "You translate documents"},
            {"role": "user", "content": "Summarize this report"},
        ]
    }
    assert analyze(body).task == "summarization"


def test_json_response_format_means_extraction_and_needs_json() -> None:
    a = _a("Give me the fields", response_format={"type": "json_object"})
    assert a.task == "extraction" and a.needs_json


def test_hard_cues_raise_complexity_into_the_high_band() -> None:
    easy = _a("Write a python function that adds two numbers")
    hard = _a("Write a production-ready python function, handle edge cases and concurrency")
    assert hard.complexity > easy.complexity
    assert hard.band == "high"


def test_nuance_and_legal_language_are_hard_cues() -> None:
    assert any("hard cues" in s for s in _a("Is this review sarcastic? One word.").signals)
    assert any("hard cues" in s for s in _a("Notwithstanding clause 9, who is liable?").signals)


def test_answer_format_does_not_make_a_task_easy() -> None:
    a = _a("Is this review sarcastic or sincere? One word.")
    assert not any("short or simple" in s for s in a.signals)
    assert any("short or simple" in s for s in _a("Briefly, what is DNS?").signals)


def test_long_input_and_requirement_lists_add_complexity() -> None:
    long_text = "Summarize this. " + "word " * 3000
    assert any("sizeable input" in s or "long input" in s for s in _a(long_text).signals)
    reqs = "Build it:\n1. a\n2. b\n3. c\n4. d"
    assert any("listed requirements" in s for s in _a(reqs).signals)


def test_tools_and_images_are_requirements() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": "data:,"}},
                ],
            }
        ],
        "tools": [{"type": "function", "function": {"name": "f"}}],
    }
    a = analyze(body)
    assert a.needs_vision and a.needs_tools


def test_caller_overrides_win_and_are_recorded() -> None:
    a = analyze(
        {"messages": [{"role": "user", "content": "hi"}]},
        task_override="coding",
        complexity_override=5,
    )
    assert (a.task, a.complexity, a.band) == ("coding", 5, "high")
    assert a.overridden == ("task", "complexity")


def test_invalid_overrides_are_ignored() -> None:
    a = analyze(
        {"messages": [{"role": "user", "content": "hi"}]},
        task_override="poetry",
        complexity_override=9,
    )
    assert a.overridden == ()


def test_output_allowance_comes_from_max_tokens_or_the_task() -> None:
    assert _a("hello", max_tokens=50).output_tokens == 50
    assert _a("Classify this as spam or ham").output_tokens == 20


def test_complexity_is_clamped() -> None:
    a = _a("Briefly classify: yes or no")
    assert 1 <= a.complexity <= 5
    assert all(t in TASKS for t in [a.task])


def test_a_tie_goes_to_the_task_whose_cue_comes_first() -> None:
    # "API" is a coding cue, but the request leads with what it wants: a summary.
    a = _a("Summarize in one sentence: the payment provider changed its API.")
    assert a.task == "summarization"
