from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast

import jwt

from production_optimizer.contracts.platform import ActorContext


class JwtIdentityPort:
    """``IdentityPort`` backed by PyJWT-verified bearer tokens.

    ``authenticate`` decodes and verifies ``credential_reference`` as a JWT
    using the configured signing key/algorithm/issuer, requiring the ``exp``,
    ``iat`` and ``sub`` claims plus custom ``tenant_id`` and ``roles``
    claims. PyJWT's own exceptions (``jwt.exceptions.PyJWTError`` and its
    subclasses, e.g. ``ExpiredSignatureError``, ``InvalidIssuerError``)
    propagate unchanged on invalid or expired tokens -- the ``IdentityPort``
    protocol declares no exception contract, so callers should catch
    ``jwt.exceptions.PyJWTError`` (and the ``ValueError`` this adapter raises
    itself for missing custom claims).

    ``authorize`` is backed by a small in-process role -> allowed-actions
    table supplied at construction. It is intentionally minimal (no
    resource-scoped rules yet, though ``resource`` is accepted for future
    extension) -- real authorization policy belongs on ``PolicyPort``/
    business logic; this exists only to make platform-level ``authorize()``
    checks meaningful.
    """

    def __init__(
        self,
        *,
        signing_key: str,
        algorithm: str = "HS256",
        issuer: str | None = None,
        authorized_actions: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        if not signing_key.strip():
            raise ValueError("signing_key must not be empty")
        if not algorithm.strip():
            raise ValueError("algorithm must not be empty")
        self._signing_key = signing_key
        self._algorithm = algorithm
        self._issuer = issuer
        self._authorized_actions: dict[str, frozenset[str]] = dict(authorized_actions or {})

    @classmethod
    def from_environment(
        cls, *, authorized_actions: Mapping[str, frozenset[str]] | None = None
    ) -> JwtIdentityPort:
        signing_key = os.environ.get("OPTIMIZER_IDENTITY_SIGNING_KEY", "").strip()
        if not signing_key:
            raise ValueError(
                "required environment variable is missing: OPTIMIZER_IDENTITY_SIGNING_KEY"
            )
        algorithm = os.environ.get("OPTIMIZER_IDENTITY_ALGORITHM", "HS256").strip() or "HS256"
        issuer = os.environ.get("OPTIMIZER_IDENTITY_ISSUER", "").strip() or None
        return cls(
            signing_key=signing_key,
            algorithm=algorithm,
            issuer=issuer,
            authorized_actions=authorized_actions,
        )

    def authenticate(self, credential_reference: str) -> ActorContext:
        payload: dict[str, Any] = jwt.decode(
            credential_reference,
            self._signing_key,
            algorithms=[self._algorithm],
            issuer=self._issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
        tenant_id = payload.get("tenant_id")
        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError("JWT is missing the required 'tenant_id' claim")
        roles = payload.get("roles")
        if not roles or not isinstance(roles, list):
            raise ValueError("JWT is missing the required non-empty 'roles' claim")
        return ActorContext(
            actor_id=str(payload["sub"]),
            tenant_id=tenant_id,
            roles={str(role) for role in cast(list[object], roles)},
            authenticated_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        )

    def authorize(self, actor: ActorContext, *, action: str, resource: str) -> bool:
        del resource  # reserved for future resource-scoped RBAC
        return any(
            action in self._authorized_actions.get(role, frozenset()) for role in actor.roles
        )
