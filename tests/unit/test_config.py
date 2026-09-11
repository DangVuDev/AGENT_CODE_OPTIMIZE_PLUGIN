from __future__ import annotations

import pytest

from production_optimizer.config import Environment, Settings

REQUIRED_ENVIRONMENT = {
    "OPTIMIZER_ENVIRONMENT": "test",
    "OPTIMIZER_POLICY_VERSION": "bootstrap-test-v1",
    "OPTIMIZER_DATABASE_DSN": "postgresql://test.invalid/optimizer",
    "OPTIMIZER_ARTIFACT_ENDPOINT": "https://artifacts.invalid",
    "OPTIMIZER_ARTIFACT_BUCKET": "test-bucket",
    "OPTIMIZER_POLICY_URL": "https://policy.invalid",
    "OPTIMIZER_OTEL_ENDPOINT": "https://otel.invalid",
}


def test_settings_load_only_declared_non_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_environment()
    assert settings.environment == Environment.TEST
    assert settings.policy_version == "bootstrap-test-v1"
    assert not hasattr(settings, "artifact_secret_key")


def test_settings_fail_closed_when_required_value_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("OPTIMIZER_DATABASE_DSN")
    with pytest.raises(ValueError, match="OPTIMIZER_DATABASE_DSN"):
        Settings.from_environment()
