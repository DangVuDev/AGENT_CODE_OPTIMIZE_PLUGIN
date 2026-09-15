from __future__ import annotations

from typing import Protocol

from production_optimizer.contracts.a2 import DecodedEvidenceObservation


class EvidenceDecoderPort(Protocol):
    """Decode a recipe's opaque worker artifact into canonical observations."""

    def decode(
        self,
        *,
        decoder_id: str,
        content: bytes,
        output_schema: str,
        value_selector: str | None,
    ) -> list[DecodedEvidenceObservation]: ...

    def healthcheck(self) -> bool: ...
