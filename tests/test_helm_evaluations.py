from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "helm" / "relay"
HELM = shutil.which("helm")

pytestmark = pytest.mark.skipif(HELM is None, reason="helm is not installed")


def _render(values_file: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        [
            HELM,
            "template",
            "relay",
            str(CHART),
            "--namespace",
            "relay",
            "-f",
            str(CHART / "examples" / values_file),
            "--show-only",
            "templates/evaluation-configmap.yaml",
            "--show-only",
            "templates/evaluation-workload.yaml",
            *extra,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


def test_retrieval_evaluation_job_uses_service_dns_and_master_secret():
    documents = _render("evaluation-job-values.yaml")
    config_map = next(document for document in documents if document["kind"] == "ConfigMap")
    job = next(document for document in documents if document["kind"] == "Job")

    assert "base_url: http://relay:8000" in config_map["data"]["eval-config.yaml"]
    assert job["spec"]["backoffLimit"] == 0
    assert job["metadata"]["annotations"]["helm.sh/hook"] == "post-install,post-upgrade"
    pod = job["spec"]["template"]["spec"]
    assert pod["initContainers"][0]["name"] == "wait-for-relay"
    secret = pod["containers"][0]["env"][0]["valueFrom"]["secretKeyRef"]
    assert secret == {"name": "relay-master-key", "key": "PROXY_MASTER_KEY"}
    assert pod["containers"][0]["volumeMounts"][0]["readOnly"] is True


def test_evaluations_are_automatically_enabled_by_inline_cases():
    documents = _render("evaluation-job-values.yaml")
    assert any(document["kind"] == "Job" for document in documents)


def test_evaluations_can_be_explicitly_disabled_without_removing_cases():
    result = subprocess.run(
        [
            HELM,
            "template",
            "relay",
            str(CHART),
            "--namespace",
            "relay",
            "-f",
            str(CHART / "examples" / "evaluation-job-values.yaml"),
            "--set",
            "evaluations.enabled=false",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    assert not any(
        document.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == "evaluator"
        for document in documents
    )


def test_generation_evaluation_cronjob_uses_dedicated_api_key():
    documents = _render("evaluation-cronjob-values.yaml")
    cron_job = next(document for document in documents if document["kind"] == "CronJob")

    assert cron_job["spec"]["concurrencyPolicy"] == "Forbid"
    assert "annotations" not in cron_job["metadata"]
    pod = cron_job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    environment = pod["containers"][0]["env"][0]
    assert environment["name"] == "RELAY_API_KEY"
    assert environment["valueFrom"]["secretKeyRef"] == {
        "name": "relay-evaluation-api-key",
        "key": "RELAY_API_KEY",
    }


def test_generation_mode_without_api_key_secret_is_rejected():
    result = subprocess.run(
        [
            HELM,
            "template",
            "relay",
            str(CHART),
            "-f",
            str(CHART / "examples" / "evaluation-job-values.yaml"),
            "--set",
            "evaluations.mode=generation",
            "--set",
            "evaluations.config.deployments[0]=general",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "evaluations.apiKeySecret is required for generation mode" in result.stderr


def test_existing_config_map_is_mounted_without_generating_inline_data():
    result = subprocess.run(
        [
            HELM,
            "template",
            "relay",
            str(CHART),
            "-f",
            str(CHART / "examples" / "evaluation-job-values.yaml"),
            "--set",
            "evaluations.existingConfigMap=curated-evaluations",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    documents = [document for document in yaml.safe_load_all(result.stdout) if document]
    assert not any(
        document["kind"] == "ConfigMap" and document["metadata"]["name"] == "relay-evaluations"
        for document in documents
    )
    job = next(document for document in documents if document["kind"] == "Job")
    config_map = job["spec"]["template"]["spec"]["volumes"][0]["configMap"]
    assert config_map["name"] == "curated-evaluations"
