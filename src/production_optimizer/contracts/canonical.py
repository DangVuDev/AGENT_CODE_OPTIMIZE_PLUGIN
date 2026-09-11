from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel


def canonical_json(value: BaseModel | Mapping[str, Any]) -> bytes:
    """Return stable UTF-8 JSON used for signatures and content identities."""

    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def model_content_digest(model: BaseModel) -> str:
    data = model.model_dump(mode="json", exclude={"content_digest"})
    return sha256_digest(canonical_json(data))


def verify_model_digest(model: BaseModel) -> bool:
    content_digest = getattr(model, "content_digest", None)
    return isinstance(content_digest, str) and content_digest == model_content_digest(model)
