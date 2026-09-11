# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import WorkerJob, WorkerReceipt

_PREFIX = "optimizer.production/"


class WorkerJobFailedError(RuntimeError):
    pass


class KubernetesWorkerBroker:
    """Submit sandboxed Kubernetes Jobs through the canonical worker protocol.

    The namespace must enforce the platform NetworkPolicy and the configured
    gVisor/equivalent RuntimeClass. Jobs additionally run non-root, without a
    service-account token, capabilities, privilege escalation or a writable
    root filesystem.
    """

    def __init__(
        self,
        *,
        namespace: str,
        image: str,
        runtime_class_name: str,
        batch_api: Any | None = None,
        load_cluster_config: bool = True,
        ttl_seconds_after_finished: int = 3600,
    ) -> None:
        if not namespace.strip() or not image.strip() or not runtime_class_name.strip():
            raise ValueError("namespace, image and runtime_class_name must not be empty")
        if ttl_seconds_after_finished < 0:
            raise ValueError("ttl_seconds_after_finished must be non-negative")
        if batch_api is None:
            if load_cluster_config:
                config.load_incluster_config()
            batch_api = client.BatchV1Api()
        self._api = batch_api
        self._namespace = namespace
        self._image = image
        self._runtime_class_name = runtime_class_name
        self._ttl_seconds = ttl_seconds_after_finished

    def submit(self, job: WorkerJob) -> WorkerReceipt:
        name = _job_name(job.job_id)
        contract = json.dumps(job.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        body = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=name,
                labels={"app.kubernetes.io/name": "production-optimizer-worker"},
                annotations={
                    f"{_PREFIX}job-id": job.job_id,
                    f"{_PREFIX}idempotency-key": job.idempotency_key,
                },
            ),
            spec=client.V1JobSpec(
                active_deadline_seconds=job.timeout_seconds,
                backoff_limit=0,
                ttl_seconds_after_finished=self._ttl_seconds,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels={"optimizer.production/job": name}),
                    spec=client.V1PodSpec(
                        automount_service_account_token=False,
                        restart_policy="Never",
                        runtime_class_name=self._runtime_class_name,
                        security_context=client.V1PodSecurityContext(
                            run_as_non_root=True,
                            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                        ),
                        containers=[
                            client.V1Container(
                                name="worker",
                                image=self._image,
                                args=["--job-json", contract],
                                security_context=client.V1SecurityContext(
                                    allow_privilege_escalation=False,
                                    capabilities=client.V1Capabilities(drop=["ALL"]),
                                    privileged=False,
                                    read_only_root_filesystem=True,
                                ),
                            )
                        ],
                    ),
                ),
            ),
        )
        try:
            self._api.create_namespaced_job(namespace=self._namespace, body=body)
        except ApiException as exc:
            if exc.status != 409:
                raise
            existing = self._api.read_namespaced_job(name=name, namespace=self._namespace)
            annotations = existing.metadata.annotations or {}
            if annotations.get(f"{_PREFIX}idempotency-key") != job.idempotency_key:
                raise WorkerJobFailedError(
                    "existing worker job has a different idempotency key"
                ) from exc
        return WorkerReceipt(
            job_id=job.job_id,
            accepted=True,
            lease_id=name,
            expires_at=datetime.now(UTC) + timedelta(seconds=job.timeout_seconds),
        )

    def cancel(self, *, job_id: str, reason: str) -> bool:
        del reason
        try:
            self._api.delete_namespaced_job(
                name=_job_name(job_id),
                namespace=self._namespace,
                propagation_policy="Background",
            )
        except ApiException as exc:
            if exc.status == 404:
                return False
            raise
        return True

    def reconcile(self, *, job_id: str, idempotency_key: str) -> ArtifactRef | None:
        try:
            job = self._api.read_namespaced_job(
                name=_job_name(job_id), namespace=self._namespace
            )
        except ApiException as exc:
            if exc.status == 404:
                return None
            raise
        annotations = job.metadata.annotations or {}
        if annotations.get(f"{_PREFIX}idempotency-key") != idempotency_key:
            raise WorkerJobFailedError("worker job idempotency identity does not match")
        if getattr(job.status, "failed", 0):
            raise WorkerJobFailedError(f"worker job {job_id!r} failed")
        if not getattr(job.status, "succeeded", 0):
            return None
        values = {
            field: annotations.get(f"{_PREFIX}output-{field}")
            for field in (
                "artifact-type",
                "schema-version",
                "artifact-id",
                "content-digest",
                "uri",
            )
        }
        if any(value is None for value in values.values()):
            raise WorkerJobFailedError(
                "completed worker job has no valid output artifact annotation"
            )
        return ArtifactRef.model_validate(
            {
                "artifact_type": values["artifact-type"],
                "schema_version": values["schema-version"],
                "artifact_id": values["artifact-id"],
                "content_digest": values["content-digest"],
                "uri": values["uri"],
            }
        )


def _job_name(job_id: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "-" for character in job_id.lower()
    ).strip("-")
    if not normalized:
        raise ValueError("job_id must contain at least one alphanumeric character")
    return f"optimizer-{normalized[:52]}".rstrip("-")
