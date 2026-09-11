# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
from __future__ import annotations

import hashlib
import os
from typing import Any, cast
from urllib.parse import urlsplit

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from production_optimizer.contracts.artifacts import ArtifactRef

_MISSING_KEY_ERROR_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


class S3ArtifactStore:
    """S3-compatible ``ArtifactStore`` (MinIO in development, real S3 in production).

    Object keys are content-addressed and tenant-isolated:
    ``f"{tenant_id}/{content_digest.removeprefix('sha256:')}"``. Identical
    content for the same tenant therefore always maps to the same key, which
    gives create-once idempotency for free -- a HEAD-before-PUT is enough to
    avoid re-uploading identical content, with no conditional-put trickery
    required.

    Known limitation: ``ArtifactStore.put_json``/``put_blob`` do not carry
    business artifact typing (``artifact_type``/``schema_version``/
    ``artifact_id``) in their signatures, so this adapter synthesizes
    generic placeholder values (``"JsonArtifact"``/``"Blob"``,
    ``schema_version="1.0"``, and ``artifact_id`` set to the content-addressed
    object key). Real per-node artifact typing will require extending the
    port signature later; that is out of scope for this adapter.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region_name: str = "us-east-1",
    ) -> None:
        if not endpoint_url.strip():
            raise ValueError("S3 endpoint URL must not be empty")
        if not bucket.strip():
            raise ValueError("S3 bucket must not be empty")
        if not access_key.strip() or not secret_key.strip():
            raise ValueError("S3 access key and secret key must not be empty")

        scheme = urlsplit(endpoint_url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"unsupported S3 endpoint URL scheme: {endpoint_url!r}")
        use_ssl = scheme == "https"

        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name,
            use_ssl=use_ssl,
            verify=use_ssl,
            config=BotoConfig(signature_version="s3v4"),
        )

    @classmethod
    def from_environment(cls) -> S3ArtifactStore:
        """Build a store from process environment variables.

        Credentials are read directly from the environment here rather than
        via ``Settings`` (which deliberately excludes secret material) or a
        full ``SecretsBroker`` adapter (out of scope for this task).
        """

        return cls(
            endpoint_url=_required_env("OPTIMIZER_ARTIFACT_ENDPOINT"),
            bucket=_required_env("OPTIMIZER_ARTIFACT_BUCKET"),
            access_key=_required_env("OPTIMIZER_ARTIFACT_ACCESS_KEY"),
            secret_key=_required_env("OPTIMIZER_ARTIFACT_SECRET_KEY"),
            region_name=os.environ.get("OPTIMIZER_ARTIFACT_REGION", "us-east-1").strip()
            or "us-east-1",
        )

    def put_json(
        self, *, tenant_id: str, content: bytes, content_digest: str, idempotency_key: str
    ) -> ArtifactRef:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be empty")
        key = self._object_key(tenant_id=tenant_id, content_digest=content_digest)
        if not self._object_exists(key):
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType="application/json",
                Metadata={"idempotency-key": idempotency_key},
            )
        return ArtifactRef(
            artifact_type="JsonArtifact",
            schema_version="1.0",
            artifact_id=key,
            content_digest=content_digest,
            uri=f"s3://{self._bucket}/{key}",
        )

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef:
        if not media_type.strip():
            raise ValueError("media_type must not be empty")
        key = self._object_key(tenant_id=tenant_id, content_digest=content_digest)
        if not self._object_exists(key):
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=media_type,
            )
        return ArtifactRef(
            artifact_type="Blob",
            schema_version="1.0",
            artifact_id=key,
            content_digest=content_digest,
            uri=f"s3://{self._bucket}/{key}",
        )

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes:
        bucket, key = self._parse_uri(ref.uri)
        if bucket != self._bucket:
            raise ValueError(
                f"artifact ref bucket {bucket!r} does not match configured bucket "
                f"{self._bucket!r}"
            )
        if not key.startswith(f"{tenant_id}/"):
            raise ValueError("artifact ref does not belong to the requesting tenant")
        response = cast("dict[str, Any]", self._client.get_object(Bucket=bucket, Key=key))
        body = response["Body"]
        return cast(bytes, body.read())

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool:
        try:
            content = self.read(tenant_id=tenant_id, ref=ref)
        except Exception:
            return False
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        return digest == ref.content_digest

    def _object_key(self, *, tenant_id: str, content_digest: str) -> str:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        return f"{tenant_id}/{content_digest.removeprefix('sha256:')}"

    def _object_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            error_response = cast("dict[str, Any]", exc.response)
            error_code = error_response.get("Error", {}).get("Code")
            if error_code in _MISSING_KEY_ERROR_CODES:
                return False
            raise
        return True

    def _parse_uri(self, uri: str) -> tuple[str, str]:
        parsed = urlsplit(uri)
        if parsed.scheme != "s3":
            raise ValueError(f"unsupported artifact URI scheme: {uri!r}")
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise ValueError(f"malformed S3 artifact URI: {uri!r}")
        return bucket, key

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> S3ArtifactStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable is missing: {name}")
    return value
