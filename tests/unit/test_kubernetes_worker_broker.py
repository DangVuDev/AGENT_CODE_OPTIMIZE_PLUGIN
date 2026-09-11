# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from kubernetes.client.exceptions import ApiException

from production_optimizer.adapters.production.kubernetes_worker_broker import (
    KubernetesWorkerBroker,
    WorkerJobFailedError,
)
from production_optimizer.contracts.platform import WorkerJob


class _BatchApi:
    def __init__(self) -> None:
        self.created: Any | None = None
        self.read_result: Any | None = None
        self.create_error: ApiException | None = None
        self.delete_error: ApiException | None = None

    def create_namespaced_job(self, *, namespace: str, body: Any) -> None:
        del namespace
        if self.create_error:
            raise self.create_error
        self.created = body

    def read_namespaced_job(self, *, name: str, namespace: str) -> Any:
        del name, namespace
        if self.read_result is None:
            raise ApiException(status=404)
        return self.read_result

    def delete_namespaced_job(self, **_: Any) -> None:
        if self.delete_error:
            raise self.delete_error


def _worker_job() -> WorkerJob:
    return WorkerJob(
        job_id="job-1",
        case_id="case-1",
        node_id="A2.60",
        idempotency_key="idem-1",
        input_refs=[],
        capability="benchmark",
        timeout_seconds=30,
    )


def _broker(api: _BatchApi) -> KubernetesWorkerBroker:
    return KubernetesWorkerBroker(
        namespace="workers",
        image="registry/worker@sha256:abc",
        runtime_class_name="gvisor",
        batch_api=api,
    )


def test_submit_builds_restricted_job() -> None:
    api = _BatchApi()
    receipt = _broker(api).submit(_worker_job())

    assert receipt.accepted is True
    assert receipt.lease_id == "optimizer-job-1"
    assert api.created is not None
    pod_spec = api.created.spec.template.spec
    assert pod_spec.automount_service_account_token is False
    assert pod_spec.runtime_class_name == "gvisor"
    assert pod_spec.containers[0].security_context.read_only_root_filesystem is True
    assert pod_spec.containers[0].security_context.capabilities.drop == ["ALL"]


def test_duplicate_submit_is_idempotent_only_for_matching_key() -> None:
    api = _BatchApi()
    api.create_error = ApiException(status=409)
    api.read_result = SimpleNamespace(
        metadata=SimpleNamespace(annotations={"optimizer.production/idempotency-key": "idem-1"})
    )
    assert _broker(api).submit(_worker_job()).accepted is True

    api.read_result.metadata.annotations["optimizer.production/idempotency-key"] = "other"
    with pytest.raises(WorkerJobFailedError, match="different idempotency"):
        _broker(api).submit(_worker_job())


def test_reconcile_returns_completed_artifact_and_rejects_failed_job() -> None:
    api = _BatchApi()
    annotations = {
        "optimizer.production/idempotency-key": "idem-1",
        "optimizer.production/output-artifact-type": "Evidence",
        "optimizer.production/output-schema-version": "1.0",
        "optimizer.production/output-artifact-id": "artifact-1",
        "optimizer.production/output-content-digest": f"sha256:{'a' * 64}",
        "optimizer.production/output-uri": "s3://bucket/key",
    }
    api.read_result = SimpleNamespace(
        metadata=SimpleNamespace(annotations=annotations),
        status=SimpleNamespace(succeeded=1, failed=0),
    )
    artifact = _broker(api).reconcile(job_id="job-1", idempotency_key="idem-1")
    assert artifact is not None
    assert artifact.artifact_id == "artifact-1"

    api.read_result.status = SimpleNamespace(succeeded=0, failed=1)
    with pytest.raises(WorkerJobFailedError, match="failed"):
        _broker(api).reconcile(job_id="job-1", idempotency_key="idem-1")


def test_cancel_and_missing_reconcile_are_safe() -> None:
    api = _BatchApi()
    assert _broker(api).reconcile(job_id="job-1", idempotency_key="idem-1") is None
    api.delete_error = ApiException(status=404)
    assert _broker(api).cancel(job_id="job-1", reason="timeout") is False
