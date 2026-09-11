# pyright: reportPrivateUsage=false
# Private helpers (_object_key/_parse_uri) are exercised directly here so
# their key-derivation and URI-parsing logic has real unit coverage without
# a live S3/MinIO endpoint; see tests/integration/test_s3_artifact_store.py
# for the network-backed round trip.
from __future__ import annotations

import pytest

from production_optimizer.adapters.production.s3_artifact_store import S3ArtifactStore
from production_optimizer.contracts.artifacts import ArtifactRef

DIGEST = "sha256:" + "b" * 64


def _store(**overrides: str) -> S3ArtifactStore:
    kwargs: dict[str, str] = {
        "endpoint_url": "http://localhost:9000",
        "bucket": "optimizer-development",
        "access_key": "optimizer",
        "secret_key": "change-me-now",
    }
    kwargs.update(overrides)
    return S3ArtifactStore(**kwargs)  # type: ignore[arg-type]


def test_constructor_rejects_empty_endpoint() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        _store(endpoint_url="  ")


def test_constructor_rejects_empty_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        _store(bucket=" ")


def test_constructor_rejects_empty_credentials() -> None:
    with pytest.raises(ValueError, match="access key"):
        _store(access_key=" ")
    with pytest.raises(ValueError, match="access key"):
        _store(secret_key=" ")


def test_constructor_rejects_unsupported_scheme() -> None:
    with pytest.raises(ValueError, match="scheme"):
        _store(endpoint_url="ftp://localhost:9000")


def test_object_key_is_content_addressed_and_tenant_scoped() -> None:
    store = _store()
    key = store._object_key(tenant_id="tenant-a", content_digest=DIGEST)
    assert key == "tenant-a/" + "b" * 64

    other_tenant_key = store._object_key(tenant_id="tenant-b", content_digest=DIGEST)
    assert other_tenant_key != key

    same_key_again = store._object_key(tenant_id="tenant-a", content_digest=DIGEST)
    assert same_key_again == key


def test_object_key_rejects_empty_tenant_id() -> None:
    store = _store()
    with pytest.raises(ValueError, match="tenant_id"):
        store._object_key(tenant_id=" ", content_digest=DIGEST)


def test_parse_uri_round_trips_bucket_and_key() -> None:
    store = _store()
    bucket, key = store._parse_uri("s3://optimizer-development/tenant-a/deadbeef")
    assert bucket == "optimizer-development"
    assert key == "tenant-a/deadbeef"


def test_parse_uri_rejects_non_s3_scheme() -> None:
    store = _store()
    with pytest.raises(ValueError, match="scheme"):
        store._parse_uri("https://example.invalid/bucket/key")


def test_parse_uri_rejects_missing_key() -> None:
    store = _store()
    with pytest.raises(ValueError, match="malformed"):
        store._parse_uri("s3://bucket-only")


def test_read_rejects_ref_from_a_different_bucket() -> None:
    store = _store(bucket="optimizer-development")
    ref = ArtifactRef(
        artifact_type="Blob",
        schema_version="1.0",
        artifact_id="tenant-a/" + "b" * 64,
        content_digest=DIGEST,
        uri="s3://some-other-bucket/tenant-a/" + "b" * 64,
    )

    with pytest.raises(ValueError, match="does not match configured bucket"):
        store.read(tenant_id="tenant-a", ref=ref)


def test_read_rejects_ref_belonging_to_a_different_tenant() -> None:
    store = _store(bucket="optimizer-development")
    ref = ArtifactRef(
        artifact_type="Blob",
        schema_version="1.0",
        artifact_id="tenant-a/" + "b" * 64,
        content_digest=DIGEST,
        uri="s3://optimizer-development/tenant-a/" + "b" * 64,
    )

    with pytest.raises(ValueError, match="does not belong to the requesting tenant"):
        store.read(tenant_id="tenant-b", ref=ref)


def test_verify_returns_false_when_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(bucket="optimizer-development")
    ref = ArtifactRef(
        artifact_type="Blob",
        schema_version="1.0",
        artifact_id="tenant-a/" + "b" * 64,
        content_digest=DIGEST,
        uri="s3://some-other-bucket/tenant-a/" + "b" * 64,
    )

    assert store.verify(tenant_id="tenant-a", ref=ref) is False


def test_close_is_idempotent_and_supports_context_manager() -> None:
    store = _store()
    with store:
        pass
    store.close()
    store.close()
