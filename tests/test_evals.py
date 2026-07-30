from evals.run import EvalResult, extract_output, extract_usage, grade_output, summarize


def test_graders_can_be_composed():
    passed, score = grade_output(
        '{"name":"Relay","status":"ok"}',
        {"contains": ["Relay", "ok"], "regex": r'"status"', "json_keys": ["name", "status"]},
    )
    assert passed
    assert score == 1.0


def test_failed_checks_return_partial_score():
    passed, score = grade_output("Helsinki", {"contains": ["Helsinki", "Finland"]})
    assert not passed
    assert score == 0.5


def test_response_and_chat_extraction():
    response_payload = {
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    assert extract_output(response_payload, "responses") == "hello"
    assert extract_usage(response_payload, "responses") == (3, 2)

    chat_payload = {
        "choices": [{"message": {"content": "hi"}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 1},
    }
    assert extract_output(chat_payload, "chat/completions") == "hi"
    assert extract_usage(chat_payload, "chat/completions") == (4, 1)


def test_summary_groups_deployments():
    rows = [
        EvalResult("a", "fast", "fast", "v1", True, 1.0, 100, 3, 2, "ok"),
        EvalResult("b", "fast", "fast", "v1", False, 0.0, 200, 4, 1, "bad"),
    ]
    summary = summarize(rows)["fast"]
    assert summary["pass_rate"] == 0.5
    assert summary["avg_latency_ms"] == 150
    assert summary["input_tokens"] == 7
