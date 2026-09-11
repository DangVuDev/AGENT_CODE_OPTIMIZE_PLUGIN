from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class Settings:
    """Non-secret process configuration.

    Secret values are intentionally represented as environment-provided
    references. Business handlers must obtain short-lived material through the
    future SecretsBroker port rather than this object.
    """

    environment: Environment
    log_level: str
    policy_version: str
    database_dsn: str
    artifact_endpoint: str
    artifact_bucket: str
    policy_url: str
    otel_endpoint: str

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            environment=Environment(_required("OPTIMIZER_ENVIRONMENT")),
            log_level=os.getenv("OPTIMIZER_LOG_LEVEL", "INFO").upper(),
            policy_version=_required("OPTIMIZER_POLICY_VERSION"),
            database_dsn=_required("OPTIMIZER_DATABASE_DSN"),
            artifact_endpoint=_required("OPTIMIZER_ARTIFACT_ENDPOINT"),
            artifact_bucket=_required("OPTIMIZER_ARTIFACT_BUCKET"),
            policy_url=_required("OPTIMIZER_POLICY_URL"),
            otel_endpoint=_required("OPTIMIZER_OTEL_ENDPOINT"),
        )


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"required environment variable is missing: {name}")
    return value
