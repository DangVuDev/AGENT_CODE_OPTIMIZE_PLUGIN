from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.artifacts import ArtifactRef


class ArtifactStore(Protocol):
    def put_json(
        self, *, tenant_id: str, content: bytes, content_digest: str, idempotency_key: str
    ) -> ArtifactRef: ...

    def put_blob(
        self, *, tenant_id: str, content: bytes, content_digest: str, media_type: str
    ) -> ArtifactRef: ...

    def read(self, *, tenant_id: str, ref: ArtifactRef) -> bytes: ...

    def verify(self, *, tenant_id: str, ref: ArtifactRef) -> bool: ...
