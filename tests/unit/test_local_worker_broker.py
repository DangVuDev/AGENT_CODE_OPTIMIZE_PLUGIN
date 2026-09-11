from __future__ import annotations

import threading
import time

import pytest

from production_optimizer.adapters.production.local_worker_broker import LocalWorkerBroker
from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import WorkerJob

RESULT_REF = ArtifactRef(
    artifact_type="Blob",
    schema_version="1.0",
    artifact_id="artifact-1",
    content_digest="sha256:" + "a" * 64,
    uri="s3://bucket/tenant/key",
)


def _job(*, job_id: str = "job-1", capability: str = "noop", timeout_seconds: int = 5) -> WorkerJob:
    return WorkerJob(
        job_id=job_id,
        case_id="case-1",
        node_id="node-1",
        idempotency_key="idem-1",
        input_refs=[],
        capability=capability,
        timeout_seconds=timeout_seconds,
        secret_refs=[],
    )


def _poll_until(predicate: object, *, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met within the timeout")


def test_submit_and_reconcile_fast_capability_returns_artifact_ref() -> None:
    broker = LocalWorkerBroker(capabilities={"noop": lambda job: RESULT_REF})
    job = _job()

    receipt = broker.submit(job)
    assert receipt.accepted is True
    assert receipt.job_id == job.job_id
    assert receipt.lease_id == job.job_id
    assert receipt.expires_at is not None

    result: ArtifactRef | None = None

    def _reconciled() -> bool:
        nonlocal result
        result = broker.reconcile(job_id=job.job_id, idempotency_key=job.idempotency_key)
        return result is not None

    _poll_until(_reconciled)
    assert result == RESULT_REF
    broker.close()


def test_submit_unsupported_capability_is_not_accepted() -> None:
    broker = LocalWorkerBroker(capabilities={})
    job = _job(capability="unknown")

    receipt = broker.submit(job)

    assert receipt.accepted is False
    assert receipt.lease_id is None
    assert receipt.expires_at is None
    broker.close()


def test_reconcile_unknown_job_id_returns_none() -> None:
    broker = LocalWorkerBroker(capabilities={})
    assert broker.reconcile(job_id="never-submitted", idempotency_key="k") is None
    broker.close()


def test_reconcile_re_raises_capability_exception() -> None:
    def _boom(job: WorkerJob) -> ArtifactRef:
        raise RuntimeError("capability failed")

    broker = LocalWorkerBroker(capabilities={"boom": _boom})
    job = _job(capability="boom")
    broker.submit(job)

    # reconcile() forgets a job once it has yielded a terminal outcome (see
    # LocalWorkerBroker docstring), so the raise must be observed on the
    # same call that first sees the future as done -- polling loop below
    # asserts on that first terminal call directly.
    deadline = time.monotonic() + 2.0
    raised: RuntimeError | None = None
    while time.monotonic() < deadline and raised is None:
        try:
            result = broker.reconcile(job_id=job.job_id, idempotency_key=job.idempotency_key)
        except RuntimeError as exc:
            raised = exc
        else:
            if result is not None:
                raise AssertionError("expected the capability to raise, not succeed")
            time.sleep(0.02)

    assert raised is not None, "capability exception was not observed within the timeout"
    assert "capability failed" in str(raised)
    broker.close()


def test_slow_capability_times_out_and_reconcile_surfaces_timeout() -> None:
    def _slow(job: WorkerJob) -> ArtifactRef:
        time.sleep(2.0)
        return RESULT_REF

    broker = LocalWorkerBroker(capabilities={"slow": _slow})
    job = _job(capability="slow", timeout_seconds=1)
    broker.submit(job)

    # As above: assert on the first call that observes the timeout, since
    # reconcile() forgets the job once it has yielded a terminal outcome.
    deadline = time.monotonic() + 3.0
    raised: TimeoutError | None = None
    while time.monotonic() < deadline and raised is None:
        try:
            result = broker.reconcile(job_id=job.job_id, idempotency_key=job.idempotency_key)
        except TimeoutError as exc:
            raised = exc
        else:
            if result is not None:
                raise AssertionError("expected the capability to time out, not succeed")
            time.sleep(0.02)

    assert raised is not None, "timeout was not observed within the polling window"
    broker.close()


def test_cancel_not_yet_started_job_prevents_it_from_running() -> None:
    started = threading.Event()
    blocker = threading.Event()

    def _blocking(job: WorkerJob) -> ArtifactRef:
        started.set()
        blocker.wait(timeout=5)
        return RESULT_REF

    ran = threading.Event()

    def _should_not_run(job: WorkerJob) -> ArtifactRef:
        ran.set()
        return RESULT_REF

    broker = LocalWorkerBroker(
        capabilities={"blocking": _blocking, "should-not-run": _should_not_run},
        max_workers=1,
    )
    first = _job(job_id="job-first", capability="blocking")
    second = _job(job_id="job-second", capability="should-not-run")

    broker.submit(first)
    assert started.wait(timeout=2), "first job should have started"

    # max_workers=1 and the first job is still occupying the only worker
    # thread, so this second job is queued but not yet started.
    receipt = broker.submit(second)
    assert receipt.accepted is True
    cancelled = broker.cancel(job_id=second.job_id, reason="not needed")
    assert cancelled is True

    blocker.set()
    time.sleep(0.2)
    assert ran.is_set() is False
    broker.close()


def test_cancel_unknown_job_returns_false() -> None:
    broker = LocalWorkerBroker(capabilities={})
    assert broker.cancel(job_id="unknown", reason="x") is False
    broker.close()


def test_constructor_rejects_non_positive_max_workers() -> None:
    with pytest.raises(ValueError, match="max_workers"):
        LocalWorkerBroker(capabilities={}, max_workers=0)
