"""Unit coverage for the CLI scripts' in-process `MemoryArtifactStore`.

Regression guard for a real production crash: `put_json` used to set
`ArtifactRef.artifact_id` to the raw `idempotency_key`, which every
`*_handlers._put_envelope` builds as
`f"{case_id}:{node_id}:{artifact_type}:{envelope.artifact_id}"`. Once
`envelope.artifact_id` is itself pass/phase-scoped (e.g.
`OPT-CLI-1-S03.80-phase-correctness-diag-1-pass0-ExecutionProvenance`),
that composite key exceeds `ArtifactRef.artifact_id`'s 100-character
limit and Pydantic raised inside `put_json` itself -- before the caller
ever got the chance to discard the field it does not use.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _lane1_common import MemoryArtifactStore  # noqa: E402

_CONTENT_DIGEST = "sha256:" + "a" * 64


def test_put_json_accepts_an_idempotency_key_longer_than_the_artifact_id_limit() -> None:
    store = MemoryArtifactStore()
    # The exact shape `s03_handlers._put_envelope` produces for a
    # pass-scoped S03.80 artifact -- 104 characters, over the limit.
    idempotency_key = (
        "OPT-CLI-1:S03.80:ExecutionProvenance:"
        "OPT-CLI-1-S03.80-phase-correctness-diag-1-pass0-ExecutionProvenance"
    )
    assert len(idempotency_key) > 100

    ref = store.put_json(
        tenant_id="TENANT-A",
        content=b"{}",
        content_digest=_CONTENT_DIGEST,
        idempotency_key=idempotency_key,
    )

    assert len(ref.artifact_id) <= 100
    # The full key still backs the uri, which is what content is actually
    # addressed and read by -- no uniqueness is lost by shortening the id.
    assert ref.uri == f"memory://{idempotency_key}"
    assert store.read(tenant_id="TENANT-A", ref=ref) == b"{}"


def test_put_json_keeps_distinct_keys_distinct() -> None:
    store = MemoryArtifactStore()
    first = store.put_json(
        tenant_id="TENANT-A",
        content=b'{"a":1}',
        content_digest=_CONTENT_DIGEST,
        idempotency_key="OPT-1:S03.80:ExecutionProvenance:" + "x" * 90,
    )
    second = store.put_json(
        tenant_id="TENANT-A",
        content=b'{"a":2}',
        content_digest=_CONTENT_DIGEST,
        idempotency_key="OPT-1:S03.80:ExecutionProvenance:" + "y" * 90,
    )

    assert first.artifact_id != second.artifact_id
    assert first.uri != second.uri
    assert store.read(tenant_id="TENANT-A", ref=first) == b'{"a":1}'
    assert store.read(tenant_id="TENANT-A", ref=second) == b'{"a":2}'
