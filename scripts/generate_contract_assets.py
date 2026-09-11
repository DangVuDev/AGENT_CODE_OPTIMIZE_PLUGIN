from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from production_optimizer.contracts.schema_registry import generated_json_schemas
from production_optimizer.orchestration.manifest import build_p2_node_manifest


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def render_assets() -> dict[Path, bytes]:
    assets = {
        Path("schemas") / f"{name}.schema.json": _json_bytes(schema)
        for name, schema in generated_json_schemas().items()
    }
    manifest = [entry.model_dump(mode="json") for entry in build_p2_node_manifest()]
    assets[Path("manifests/node-manifest.json")] = _json_bytes(manifest)
    return assets


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate version-controlled P2 contract assets")
    parser.add_argument("--check", action="store_true", help="fail if generated assets differ")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    stale: list[str] = []
    for relative_path, expected in render_assets().items():
        target = root / relative_path
        if arguments.check:
            if not target.is_file() or target.read_bytes() != expected:
                stale.append(relative_path.as_posix())
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(expected)
    if stale:
        raise SystemExit(f"generated contract assets are stale: {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
