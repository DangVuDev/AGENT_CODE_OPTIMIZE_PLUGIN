from __future__ import annotations

from pathlib import Path

import pytest

from production_optimizer.adapters.production.migrations import (
    MigrationError,
    apply_migrations,
    migration_version,
    strip_transaction_wrapper,
)


def test_migration_version_parses_numeric_prefix() -> None:
    assert migration_version("001_control_plane.sql") == 1
    assert migration_version("002_node_intents_artifact_ref.sql") == 2
    assert migration_version("123_future_change.sql") == 123


def test_migration_version_rejects_filename_without_numeric_prefix() -> None:
    with pytest.raises(MigrationError, match="numeric version prefix"):
        migration_version("control_plane.sql")


def test_strip_transaction_wrapper_removes_begin_and_commit() -> None:
    sql = "BEGIN;\n\nCREATE TABLE t (id int);\n\nCOMMIT;\n"
    assert strip_transaction_wrapper(sql) == "\nCREATE TABLE t (id int);\n"


def test_strip_transaction_wrapper_is_case_insensitive() -> None:
    sql = "begin;\nSELECT 1;\ncommit;"
    assert strip_transaction_wrapper(sql) == "SELECT 1;"


def test_strip_transaction_wrapper_leaves_unwrapped_sql_unchanged() -> None:
    sql = "ALTER TABLE t ADD COLUMN IF NOT EXISTS c text;"
    assert strip_transaction_wrapper(sql) == sql


def test_apply_migrations_rejects_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(MigrationError, match="does not exist"):
        apply_migrations("postgresql://unused.invalid/optimizer", migrations_dir=missing)
