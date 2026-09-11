from __future__ import annotations

from typing import Any

import pytest

from production_optimizer.contracts.a3 import A3QualityReport, FindingSet, SolutionPortfolio
from production_optimizer.contracts.b1 import DetectionReport, QualifiedOpportunity
from production_optimizer.contracts.b2 import ProposalEnvelope
from production_optimizer.contracts.c0 import ConvergedCase, ConvergenceDecision
from production_optimizer.contracts.envelope import ArtifactEnvelope

NEW_SEALED_ARTIFACTS: list[type[ArtifactEnvelope]] = [
    FindingSet,
    SolutionPortfolio,
    A3QualityReport,
    DetectionReport,
    QualifiedOpportunity,
    ProposalEnvelope,
    ConvergenceDecision,
    ConvergedCase,
]


@pytest.mark.parametrize("artifact_cls", NEW_SEALED_ARTIFACTS, ids=lambda cls: cls.__name__)
def test_sealed_artifact_schema_generates_without_error(
    artifact_cls: type[ArtifactEnvelope],
) -> None:
    schema: dict[str, Any] = artifact_cls.model_json_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema
    assert "artifact_type" in schema["properties"]
    assert "schema_version" in schema["properties"]


@pytest.mark.parametrize("artifact_cls", NEW_SEALED_ARTIFACTS, ids=lambda cls: cls.__name__)
def test_sealed_artifact_type_and_version_are_pinned_constants(
    artifact_cls: type[ArtifactEnvelope],
) -> None:
    schema: dict[str, Any] = artifact_cls.model_json_schema()
    artifact_type_schema = schema["properties"]["artifact_type"]
    schema_version_schema = schema["properties"]["schema_version"]

    expected_artifact_type = artifact_cls.model_fields["artifact_type"].default
    expected_schema_version = artifact_cls.model_fields["schema_version"].default

    assert artifact_type_schema.get("const") == expected_artifact_type
    assert schema_version_schema.get("const") == expected_schema_version
