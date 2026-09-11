from __future__ import annotations

import re
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from psycopg.rows import DictRow, dict_row

from production_optimizer.contracts.canonical import sha256_digest

# src/production_optimizer/adapters/production/migrations.py -> repo root.
_DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "migrations"
_VERSION_PATTERN = re.compile(r"^(\d+)_")

# `optimizer_control.schema_migrations` must be queryable before any migration
# file can be checked against it, so this minimal bootstrap (verbatim from
# 001_control_plane.sql's schema/table declarations, both already
# `IF NOT EXISTS`) is applied unconditionally before the file loop below. It
# never diverges from 001's own copy because that file is itself replayed
# through the same loop immediately afterwards.
_BOOTSTRAP_SQL = """
CREATE SCHEMA IF NOT EXISTS optimizer_control;
CREATE TABLE IF NOT EXISTS optimizer_control.schema_migrations (
    version bigint PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now(),
    checksum text NOT NULL
);
"""


class MigrationError(RuntimeError):
    """Raised when migration files cannot be safely applied (e.g. checksum drift)."""


def migration_version(filename: str) -> int:
    """Parse the numeric version prefix from a migration filename.

    ``001_control_plane.sql`` -> ``1``. Raises `MigrationError` if the
    filename does not start with digits followed by an underscore.
    """

    match = _VERSION_PATTERN.match(filename)
    if match is None:
        raise MigrationError(
            f"migration filename {filename!r} does not start with a numeric version prefix"
        )
    return int(match.group(1))


def strip_transaction_wrapper(sql: str) -> str:
    """Remove an enclosing ``BEGIN;``/``COMMIT;`` pair so the caller owns the transaction.

    Migration files are also mounted directly into a Postgres container's
    ``docker-entrypoint-initdb.d``, where each must be a valid standalone
    script (hence its own ``BEGIN``/``COMMIT``). When `apply_migrations` runs a
    file itself it needs full control of the transaction so the
    ``schema_migrations`` bookkeeping row commits atomically together with the
    migration, so a leading ``BEGIN;`` and trailing ``COMMIT;`` line -- if
    present -- are stripped before execution. Files without the wrapper are
    returned unchanged.
    """

    lines = sql.strip("\n").splitlines()
    if lines and lines[0].strip().rstrip(";").upper() == "BEGIN":
        lines = lines[1:]
    if lines and lines[-1].strip().rstrip(";").upper() == "COMMIT":
        lines = lines[:-1]
    return "\n".join(lines)


def apply_migrations(
    dsn: str, *, migrations_dir: Path = _DEFAULT_MIGRATIONS_DIR
) -> list[str]:
    """Apply every ``*.sql`` file under `migrations_dir` not yet recorded.

    Files are named ``<version>_<name>.sql``; the numeric prefix becomes
    ``optimizer_control.schema_migrations.version``. Each file's checksum is
    the `sha256_digest` (the same ``sha256:<hex>``-prefixed form used
    elsewhere in the contracts layer) of its raw UTF-8 bytes.

    A version already recorded with a matching checksum is skipped. A version
    already recorded with a *different* checksum raises `MigrationError`
    (drift between the deployed schema and this checkout) rather than
    silently re-applying or ignoring the file. An unapplied file is executed
    in full (its own transaction wrapper stripped first, see
    `strip_transaction_wrapper`) and its `schema_migrations` row is inserted
    in the same transaction before committing.

    Returns the filenames that were newly applied, in the order applied.
    """

    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory does not exist: {migrations_dir}")

    files = sorted(migrations_dir.glob("*.sql"), key=lambda path: path.name)
    applied: list[str] = []

    with psycopg.Connection[DictRow].connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(_BOOTSTRAP_SQL)
        conn.commit()

        for path in files:
            version = migration_version(path.name)
            content = path.read_text(encoding="utf-8")
            checksum = sha256_digest(content.encode("utf-8"))

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT checksum FROM optimizer_control.schema_migrations "
                    "WHERE version = %s",
                    (version,),
                )
                row = cur.fetchone()

            if row is not None:
                if row["checksum"] != checksum:
                    raise MigrationError(
                        f"migration {path.name!r} (version {version}) was already applied "
                        "with a different checksum; refusing to re-apply it (this indicates "
                        "drift between the deployed schema and the migration file on disk)"
                    )
                continue

            statement = strip_transaction_wrapper(content)
            with conn.cursor() as cur:
                # psycopg 3.3's `execute()` normally demands a `LiteralString` to
                # guard against interpolated-SQL injection; this string is trusted
                # migration file content read from disk, not user input, so the
                # cast is a deliberate opt-out of that check, not a workaround.
                cur.execute(cast(LiteralString, statement))
                cur.execute(
                    "INSERT INTO optimizer_control.schema_migrations (version, checksum) "
                    "VALUES (%s, %s)",
                    (version, checksum),
                )
            conn.commit()
            applied.append(path.name)

    return applied
