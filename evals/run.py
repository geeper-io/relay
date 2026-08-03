"""Run repeatable evaluations against Relay deployment aliases.

Usage:
    python -m evals.run --config evals/config.example.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml


@dataclass
class EvalResult:
    case_id: str
    requested_deployment: str
    routed_deployment: str
    policy_version: str
    passed: bool
    score: float
    latency_ms: int
    input_tokens: int
    output_tokens: int
    output: str
    error: str | None = None


@dataclass
class RetrievalEvalResult:
    case_id: str
    passed: bool
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    ranked_ids: list[str]
    relevant_ids: list[str]
    latency_ms: int
    error: str | None = None


def grade_output(output: str, expected: dict[str, Any]) -> tuple[bool, float]:
    checks: list[bool] = []
    if "equals" in expected:
        checks.append(output.strip() == str(expected["equals"]).strip())
    if "contains" in expected:
        values = expected["contains"]
        values = [values] if isinstance(values, str) else values
        checks.extend(str(value).lower() in output.lower() for value in values)
    if "not_contains" in expected:
        values = expected["not_contains"]
        values = [values] if isinstance(values, str) else values
        checks.extend(str(value).lower() not in output.lower() for value in values)
    if "citations" in expected:
        values = expected["citations"]
        values = [values] if isinstance(values, str) else values
        checks.extend(str(value).lower() in output.lower() for value in values)
    if "regex" in expected:
        checks.append(re.search(str(expected["regex"]), output, re.MULTILINE) is not None)
    if "json_keys" in expected:
        try:
            payload = json.loads(output)
            checks.extend(key in payload for key in expected["json_keys"])
        except (json.JSONDecodeError, TypeError):
            checks.append(False)
    if not checks:
        return True, 1.0
    score = sum(checks) / len(checks)
    return all(checks), score


def retrieval_metrics(ranked_ids: list[str], relevant_ids: list[str], k: int) -> tuple[float, float, float]:
    relevant = set(relevant_ids)
    if not relevant:
        return 1.0, 1.0, 1.0
    ranked = ranked_ids[:k]
    hits = [index for index, doc_id in enumerate(ranked, start=1) if doc_id in relevant]
    recall = len({doc_id for doc_id in ranked if doc_id in relevant}) / len(relevant)
    reciprocal_rank = 1 / hits[0] if hits else 0.0
    dcg = sum(1 / math.log2(rank + 1) for rank in hits)
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    ndcg = dcg / ideal_dcg if ideal_dcg else 1.0
    return recall, reciprocal_rank, ndcg


def extract_output(payload: dict[str, Any], endpoint: str) -> str:
    if endpoint == "chat/completions":
        choices = payload.get("choices", [])
        return str(choices[0].get("message", {}).get("content", "")) if choices else ""
    texts: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text":
                texts.append(str(part.get("text", "")))
    return "".join(texts)


def extract_usage(payload: dict[str, Any], endpoint: str) -> tuple[int, int]:
    usage = payload.get("usage") or {}
    if endpoint == "chat/completions":
        return int(usage.get("prompt_tokens", 0) or 0), int(usage.get("completion_tokens", 0) or 0)
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


async def run_case(
    client: httpx.AsyncClient,
    *,
    endpoint: str,
    deployment: str,
    case: dict[str, Any],
) -> EvalResult:
    started = time.monotonic()
    case_id = str(case.get("id", "unnamed"))
    try:
        if endpoint == "chat/completions":
            body = {
                "model": deployment,
                "messages": case.get("messages") or [{"role": "user", "content": case["input"]}],
                **case.get("parameters", {}),
            }
        else:
            body = {
                "model": deployment,
                "input": case.get("messages") or case["input"],
                "store": False,
                **case.get("parameters", {}),
            }
        response = await client.post(f"/v1/{endpoint}", json=body)
        response.raise_for_status()
        payload = response.json()
        output = extract_output(payload, endpoint)
        passed, score = grade_output(output, case.get("expected", {}))
        input_tokens, output_tokens = extract_usage(payload, endpoint)
        return EvalResult(
            case_id=case_id,
            requested_deployment=deployment,
            routed_deployment=response.headers.get("x-relay-deployment", "unknown"),
            policy_version=response.headers.get("x-relay-policy-version", "unknown"),
            passed=passed,
            score=score,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            output=output,
        )
    except Exception as exc:
        return EvalResult(
            case_id=case_id,
            requested_deployment=deployment,
            routed_deployment="unknown",
            policy_version="unknown",
            passed=False,
            score=0.0,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=0,
            output_tokens=0,
            output="",
            error=str(exc),
        )


def summarize(results: list[EvalResult]) -> dict[str, Any]:
    by_deployment: dict[str, list[EvalResult]] = {}
    for result in results:
        by_deployment.setdefault(result.requested_deployment, []).append(result)

    summary: dict[str, Any] = {}
    for deployment, rows in by_deployment.items():
        latencies = sorted(row.latency_ms for row in rows)
        p95_index = max(0, int(len(latencies) * 0.95) - 1)
        summary[deployment] = {
            "cases": len(rows),
            "passed": sum(row.passed for row in rows),
            "pass_rate": round(sum(row.passed for row in rows) / len(rows), 4),
            "mean_score": round(statistics.mean(row.score for row in rows), 4),
            "avg_latency_ms": round(statistics.mean(latencies), 1),
            "p95_latency_ms": latencies[p95_index],
            "input_tokens": sum(row.input_tokens for row in rows),
            "output_tokens": sum(row.output_tokens for row in rows),
        }
    return summary


def summarize_retrieval(results: list[RetrievalEvalResult], k: int) -> dict[str, Any]:
    if not results:
        return {"cases": 0, "passed": 0, "pass_rate": 0.0}
    return {
        "cases": len(results),
        "passed": sum(result.passed for result in results),
        "pass_rate": round(sum(result.passed for result in results) / len(results), 4),
        f"mean_recall@{k}": round(statistics.mean(result.recall_at_k for result in results), 4),
        "mrr": round(statistics.mean(result.reciprocal_rank for result in results), 4),
        f"mean_ndcg@{k}": round(statistics.mean(result.ndcg_at_k for result in results), 4),
        "avg_latency_ms": round(statistics.mean(result.latency_ms for result in results), 1),
    }


async def run_retrieval(
    client: httpx.AsyncClient,
    cases: list[dict[str, Any]],
    *,
    k: int,
    minimum_recall: float,
) -> list[RetrievalEvalResult]:
    results: list[RetrievalEvalResult] = []
    for case in cases:
        started = time.monotonic()
        case_id = str(case.get("id", "unnamed"))
        relevant_ids = [str(item) for item in case.get("relevant_ids", [])]
        try:
            if not relevant_ids:
                raise ValueError("retrieval cases require at least one relevant_ids entry")
            params: dict[str, Any] = {"q": case["query"], "n": k}
            if case.get("repo"):
                params["repo"] = case["repo"]
            response = await client.get("/internal/kb/search", params=params)
            response.raise_for_status()
            ranked_ids = [str(item["doc_id"]) for item in response.json().get("results", [])]
            recall, reciprocal_rank, ndcg = retrieval_metrics(ranked_ids, relevant_ids, k)
            results.append(
                RetrievalEvalResult(
                    case_id=case_id,
                    passed=recall >= float(case.get("minimum_recall", minimum_recall)),
                    recall_at_k=recall,
                    reciprocal_rank=reciprocal_rank,
                    ndcg_at_k=ndcg,
                    ranked_ids=ranked_ids,
                    relevant_ids=relevant_ids,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
            )
        except Exception as exc:
            results.append(
                RetrievalEvalResult(
                    case_id=case_id,
                    passed=False,
                    recall_at_k=0.0,
                    reciprocal_rank=0.0,
                    ndcg_at_k=0.0,
                    ranked_ids=[],
                    relevant_ids=relevant_ids,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error=str(exc),
                )
            )
    return results


async def run(config_path: Path) -> tuple[dict[str, Any], bool]:
    config = yaml.safe_load(config_path.read_text()) or {}
    dataset_path = (config_path.parent / config["dataset"]).resolve()
    cases = [json.loads(line) for line in dataset_path.read_text().splitlines() if line.strip()]
    api_key = os.environ.get(config.get("api_key_env", "RELAY_API_KEY"), "")
    if not api_key:
        raise RuntimeError(f"Set {config.get('api_key_env', 'RELAY_API_KEY')} before running evaluations")

    limits = httpx.Limits(max_connections=int(config.get("concurrency", 4)))
    async with httpx.AsyncClient(
        base_url=str(config.get("base_url", "http://localhost:8000")).rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=float(config.get("timeout_seconds", 120)),
        limits=limits,
    ) as client:
        if config.get("mode", "generation") == "retrieval":
            k = int(config.get("k", 5))
            results = await run_retrieval(
                client,
                cases,
                k=k,
                minimum_recall=float(config.get("minimum_recall", 1.0)),
            )
            report = {
                "config": config,
                "summary": summarize_retrieval(results, k),
                "results": [asdict(result) for result in results],
            }
            output_path = (config_path.parent / config.get("output", "retrieval-results.json")).resolve()
            output_path.write_text(json.dumps(report, indent=2) + "\n")
            return report, all(result.passed for result in results)

        deployments = [str(item) for item in config["deployments"]]
        if not deployments:
            raise RuntimeError("Generation evaluations require at least one deployment")
        endpoint = str(config.get("endpoint", "responses")).strip("/")
        semaphore = asyncio.Semaphore(int(config.get("concurrency", 4)))

        async def limited(deployment: str, case: dict[str, Any]) -> EvalResult:
            async with semaphore:
                return await run_case(client, endpoint=endpoint, deployment=deployment, case=case)

        results = await asyncio.gather(*(limited(deployment, case) for deployment in deployments for case in cases))

    report = {
        "config": {key: value for key, value in config.items() if key != "api_key"},
        "summary": summarize(results),
        "results": [asdict(result) for result in results],
    }
    output_path = (config_path.parent / config.get("output", "results.json")).resolve()
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    return report, all(result.passed for result in results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Relay deployment aliases")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    report, passed = asyncio.run(run(args.config.resolve()))
    print(json.dumps(report["summary"], indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
