from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from production_optimizer.contracts.schema_registry import CONTRACT_MODELS
from production_optimizer.orchestration.catalog import ALL_BUSINESS_NODE_IDS

ROOT = Path(__file__).resolve().parents[2]


def test_generated_contract_assets_are_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/generate_contract_assets.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_every_registered_contract_has_a_committed_schema() -> None:
    generated = {
        path.name.removesuffix(".schema.json")
        for path in (ROOT / "schemas").glob("*.schema.json")
    }
    assert generated == set(CONTRACT_MODELS)


def test_node_manifest_is_exhaustive_unique_and_fail_closed() -> None:
    entries = json.loads((ROOT / "manifests/node-manifest.json").read_text(encoding="utf-8"))
    ids = [entry["node_id"] for entry in entries]
    assert len(ids) == 99
    assert len(ids) == len(set(ids))
    assert set(ids) == ALL_BUSINESS_NODE_IDS
    assert all(entry["readiness"] == "disabled" for entry in entries)
    assert all(entry["enabled"] is False for entry in entries)
    assert all(entry["handler"] is None for entry in entries)
