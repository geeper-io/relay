import httpx
import pytest

from evals.run import (
    EvalResult,
    RetrievalEvalResult,
    extract_output,
    extract_usage,
    grade_output,
    retrieval_metrics,
    run_retrieval,
    summarize,
    summarize_retrieval,
)


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


def test_negative_and_citation_graders_cover_leakage_and_grounding():
    passed, score = grade_output(
        "Rotate the token immediately. [security-runbook]",
        {"not_contains": ["sk-secret-value"], "citations": ["security-runbook"]},
    )
    assert passed
    assert score == 1.0


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


def test_retrieval_metrics_and_summary():
    recall, reciprocal_rank, ndcg = retrieval_metrics(
        ["noise", "relevant-a", "relevant-b"],
        ["relevant-a", "relevant-b"],
        3,
    )
    assert recall == 1.0
    assert reciprocal_rank == 0.5
    assert 0.69 < ndcg < 0.7

    summary = summarize_retrieval(
        [
            RetrievalEvalResult(
                "case",
                True,
                recall,
                reciprocal_rank,
                ndcg,
                ["noise", "relevant-a", "relevant-b"],
                ["relevant-a", "relevant-b"],
                20,
            )
        ],
        3,
    )
    assert summary["mean_recall@3"] == 1.0
    assert summary["mrr"] == 0.5


@pytest.mark.asyncio
async def test_retrieval_runner_uses_ranked_debug_endpoint():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/kb/search"
        assert request.url.params["n"] == "3"
        return httpx.Response(200, json={"results": [{"doc_id": "noise"}, {"doc_id": "auth"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://relay.test") as client:
        results = await run_retrieval(
            client,
            [{"id": "auth", "query": "authentication", "relevant_ids": ["auth"]}],
            k=3,
            minimum_recall=1.0,
        )

    assert results[0].passed
    assert results[0].reciprocal_rank == 0.5


@pytest.mark.asyncio
async def test_retrieval_case_without_labels_fails_closed():
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        results = await run_retrieval(
            client,
            [{"id": "unlabelled", "query": "authentication", "relevant_ids": []}],
            k=3,
            minimum_recall=1.0,
        )

    assert not results[0].passed
    assert "relevant_ids" in results[0].error
