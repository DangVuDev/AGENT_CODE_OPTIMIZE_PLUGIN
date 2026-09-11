from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from production_optimizer.contracts.artifacts import ArtifactRef
from production_optimizer.contracts.platform import WorkerJob, WorkerReceipt

CapabilityFn = Callable[[WorkerJob], ArtifactRef]


class LocalWorkerBroker:
    """In-process ``WorkerBroker``.

    P1 scope is "protocol + local broker only" -- a real distributed/
    Kubernetes-backed broker is deferred to a later stage. Jobs are
    dispatched to callables from a capability table supplied at
    construction, keyed by ``WorkerJob.capability``, and run on a
    ``ThreadPoolExecutor``.

    Each accepted job gets its own dedicated single-thread executor that
    runs the capability callable, while a wrapper task (submitted to the
    shared pool) waits on that dedicated future with
    ``future.result(timeout=job.timeout_seconds)``. This gives every job a
    hard per-job cutoff -- the tracked future completes-or-raises
    ``TimeoutError`` at the deadline, observable later via ``reconcile()`` --
    without one slow job's dedicated thread starving the shared pool (a
    naive design that resubmits the capability onto the *same* shared pool
    can deadlock when ``max_workers`` is small, since the wrapper occupies a
    slot while waiting for a capability slot that may never free up).

    Known limitation: in-flight job bookkeeping lives only in this
    process's memory. ``reconcile()`` can recover from a caller's
    crash-and-retry loop *within the same still-running process*, but
    cannot survive an actual process restart -- durable crash recovery
    needs a persistent in-flight record (e.g. the outbox/node_intents
    tables owned by another adapter), not this broker.
    """

    def __init__(self, *, capabilities: Mapping[str, CapabilityFn], max_workers: int = 4) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        self._capabilities: dict[str, CapabilityFn] = dict(capabilities)
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future[ArtifactRef]] = {}

    def submit(self, job: WorkerJob) -> WorkerReceipt:
        fn = self._capabilities.get(job.capability)
        if fn is None:
            return WorkerReceipt(job_id=job.job_id, accepted=False)

        def _run() -> ArtifactRef:
            job_executor = ThreadPoolExecutor(max_workers=1)
            job_future = job_executor.submit(fn, job)
            try:
                return job_future.result(timeout=job.timeout_seconds)
            finally:
                # wait=False: never block the wrapper on a capability that
                # is still running past its own deadline.
                job_executor.shutdown(wait=False)

        future = self._executor.submit(_run)
        self._futures[job.job_id] = future
        expires_at = datetime.now(UTC) + timedelta(seconds=job.timeout_seconds)
        return WorkerReceipt(
            job_id=job.job_id, accepted=True, lease_id=job.job_id, expires_at=expires_at
        )

    def cancel(self, *, job_id: str, reason: str) -> bool:
        del reason
        future = self._futures.pop(job_id, None)
        if future is None:
            return False
        return future.cancel()

    def reconcile(self, *, job_id: str, idempotency_key: str) -> ArtifactRef | None:
        del idempotency_key
        future = self._futures.get(job_id)
        if future is None:
            return None
        if not future.done():
            return None
        self._futures.pop(job_id, None)
        return future.result()

    def close(self) -> None:
        self._executor.shutdown(wait=True)

    def __enter__(self) -> LocalWorkerBroker:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
