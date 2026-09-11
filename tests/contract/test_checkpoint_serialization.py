from __future__ import annotations

import pytest

from production_optimizer.adapters.production import (
    PostgresCheckpointProvider,
    strict_checkpoint_serializer,
)
from production_optimizer.contracts import ArtifactRef


def test_checkpoint_serializer_round_trips_allowlisted_contract() -> None:
    serializer = strict_checkpoint_serializer()
    original = ArtifactRef(
        artifact_type="OptimizationRequest",
        schema_version="1.0",
        artifact_id="ART-1",
        content_digest=f"sha256:{'a' * 64}",
        uri="s3://tenant/ART-1",
    )
    payload = serializer.dumps_typed(original)
    restored = serializer.loads_typed(payload)
    assert restored == original


def test_checkpoint_provider_is_fail_closed_before_startup() -> None:
    provider = PostgresCheckpointProvider("postgresql://localhost/optimizer")
    assert provider.healthcheck() is False
    with pytest.raises(RuntimeError, match="has not been set up"):
        provider.checkpointer()


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(0, 1), (2, 1)],
)
def test_checkpoint_provider_rejects_invalid_pool_bounds(minimum: int, maximum: int) -> None:
    with pytest.raises(ValueError, match="pool bounds"):
        PostgresCheckpointProvider(
            "postgresql://localhost/optimizer",
            min_pool_size=minimum,
            max_pool_size=maximum,
        )
