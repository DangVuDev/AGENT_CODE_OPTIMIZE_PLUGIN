from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from production_optimizer.adapters.production.jwt_identity import JwtIdentityPort
from production_optimizer.contracts.platform import ActorContext

SIGNING_KEY = "unit-test-signing-key-at-least-32-bytes-long"
ISSUER = "https://issuer.invalid"
_UNSET = object()


def _token(
    *,
    sub: str = "actor-1",
    tenant_id: str | None = "tenant-a",
    roles: list[str] | object | None = _UNSET,
    issuer: str | None = ISSUER,
    iat: datetime | None = None,
    exp: datetime | None = None,
    extra_claims: dict[str, object] | None = None,
) -> str:
    if roles is _UNSET:
        roles = ["engineer"]
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": sub,
        "iat": iat or now,
        "exp": exp or (now + timedelta(minutes=5)),
    }
    if issuer is not None:
        claims["iss"] = issuer
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    if roles is not None:
        claims["roles"] = roles
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, SIGNING_KEY, algorithm="HS256")


def _port(**kwargs: object) -> JwtIdentityPort:
    defaults: dict[str, object] = {"signing_key": SIGNING_KEY, "issuer": ISSUER}
    defaults.update(kwargs)
    return JwtIdentityPort(**defaults)  # type: ignore[arg-type]


def test_valid_token_round_trips_to_actor_context() -> None:
    port = _port()
    token = _token(sub="actor-1", tenant_id="tenant-a", roles=["engineer", "reviewer"])

    actor = port.authenticate(token)

    assert isinstance(actor, ActorContext)
    assert actor.actor_id == "actor-1"
    assert actor.tenant_id == "tenant-a"
    assert actor.roles == {"engineer", "reviewer"}
    assert actor.authenticated_at.tzinfo is not None


def test_expired_token_raises() -> None:
    port = _port()
    token = _token(
        iat=datetime.now(UTC) - timedelta(hours=1),
        exp=datetime.now(UTC) - timedelta(minutes=1),
    )

    with pytest.raises(jwt.exceptions.ExpiredSignatureError):
        port.authenticate(token)


def test_missing_tenant_id_claim_raises_clear_error() -> None:
    port = _port()
    token = _token(tenant_id=None)

    with pytest.raises(ValueError, match="tenant_id"):
        port.authenticate(token)


def test_missing_roles_claim_raises_clear_error() -> None:
    port = _port()
    token = _token(roles=None)

    with pytest.raises(ValueError, match="roles"):
        port.authenticate(token)


def test_empty_roles_claim_raises_clear_error() -> None:
    port = _port()
    token = _token(roles=[])

    with pytest.raises(ValueError, match="roles"):
        port.authenticate(token)


def test_wrong_issuer_raises() -> None:
    port = _port()
    token = _token(issuer="https://someone-else.invalid")

    with pytest.raises(jwt.exceptions.InvalidIssuerError):
        port.authenticate(token)


def test_wrong_signing_key_raises() -> None:
    port = _port()
    token = jwt.encode(
        {
            "sub": "actor-1",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
            "iss": ISSUER,
            "tenant_id": "tenant-a",
            "roles": ["engineer"],
        },
        "a-completely-different-key-also-32-bytes-plus",
        algorithm="HS256",
    )

    with pytest.raises(jwt.exceptions.InvalidSignatureError):
        port.authenticate(token)


@pytest.mark.parametrize(
    ("roles", "action", "expected"),
    [
        ({"engineer"}, "run:optimize", True),
        ({"engineer"}, "approve:release", False),
        ({"approver"}, "approve:release", True),
        ({"engineer", "approver"}, "approve:release", True),
        ({"unassigned"}, "run:optimize", False),
    ],
)
def test_authorize_across_role_combinations(
    roles: set[str], action: str, expected: bool
) -> None:
    port = _port(
        authorized_actions={
            "engineer": frozenset({"run:optimize"}),
            "approver": frozenset({"approve:release"}),
        }
    )
    actor = ActorContext(
        actor_id="actor-1",
        tenant_id="tenant-a",
        roles=roles,
        authenticated_at=datetime.now(UTC),
    )

    assert port.authorize(actor, action=action, resource="case:1") is expected


def test_constructor_rejects_empty_signing_key() -> None:
    with pytest.raises(ValueError, match="signing_key"):
        JwtIdentityPort(signing_key="  ")
