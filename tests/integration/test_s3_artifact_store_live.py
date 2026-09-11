"""Integration coverage for S3ArtifactStore against a live MinIO instance.

This is a SUPPLEMENT to the in-memory/pure unit coverage in
``tests/unit/test_s3_artifact_store.py``, not a substitute for it. It only
runs when the development compose stack's MinIO service (see
``deploy/development/compose.yaml``) is reachable; otherwise it skips,
following the same skip-if-unreachable convention used for the PostgreSQL
integration tests.
"""

# pyright: reportPrivateUsage=false, reportUnknownMemberType=false
from __future__ import annotations

import hashlib

import pytest

from production_optimizer.adapters.production.s3_artifact_store import S3ArtifactStore

ENDPOINT_URL = "http://127.0.0.1:9000"
BUCKET = "optimizer-development"
ACCESS_KEY = "optimizer"
SECRET_KEY = "change-me-now"


def _require_minio() -> S3ArtifactStore:
    store = S3ArtifactStore(
        endpoint_url=ENDPOINT_URL,
        bucket=BUCKET,
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
    )
    try:
        store._client.head_bucket(Bucket=BUCKET)
    except Exception as exc:  # pragma: no cover - depends on local infra
        pytest.skip(f"MinIO is not reachable at {ENDPOINT_URL}: {exc}")
    return store


def test_put_json_is_idempotent_and_round_trips() -> None:
    store = _require_minio()
    content = b'{"hello": "world"}'
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"

    first = store.put_json(
        tenant_id="tenant-integration",
        content=content,
        content_digest=digest,
        idempotency_key="idem-1",
    )
    second = store.put_json(
        tenant_id="tenant-integration",
        content=content,
        content_digest=digest,
        idempotency_key="idem-2",
    )

    assert first.uri == second.uri
    assert first.content_digest == digest

    read_back = store.read(tenant_id="tenant-integration", ref=first)
    assert read_back == content
    assert store.verify(tenant_id="tenant-integration", ref=first) is True

    store.close()


def test_put_blob_round_trips_with_media_type() -> None:
    store = _require_minio()
    content = b"binary-ish content"
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"

    ref = store.put_blob(
        tenant_id="tenant-integration",
        content=content,
        content_digest=digest,
        media_type="application/octet-stream",
    )

    assert store.read(tenant_id="tenant-integration", ref=ref) == content
    store.close()
