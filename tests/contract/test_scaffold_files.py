import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_langgraph_exports_only_the_root_graph() -> None:
    config = json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))
    assert set(config["graphs"]) == {"optimization_root"}
    assert config["graphs"]["optimization_root"].endswith("root_graph.py:graph")


def test_production_deployment_cannot_reuse_development_compose() -> None:
    production_files = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "deploy" / "production").rglob("*")
        if path.is_file()
    }
    assert production_files == {"deploy/production/README.md"}


def test_example_environment_contains_no_real_secret_file() -> None:
    assert not (ROOT / ".env").exists()
    assert (ROOT / ".env.example").is_file()
