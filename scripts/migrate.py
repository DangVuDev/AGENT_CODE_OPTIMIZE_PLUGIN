"""Standalone CLI entrypoint: apply pending control-plane database migrations.

Deliberately does not use `production_optimizer.config.Settings.from_environment`,
which requires six unrelated environment variables (artifact store, policy
engine, OTEL endpoint, ...); this script only needs the database DSN, so it
reads `OPTIMIZER_DATABASE_DSN` directly and stays usable standalone (e.g. in a
minimal migration-only CI step or container).
"""

from __future__ import annotations

import os
import sys

from production_optimizer.adapters.production.migrations import apply_migrations


def main() -> int:
    dsn = os.environ.get("OPTIMIZER_DATABASE_DSN", "").strip()
    if not dsn:
        print(
            "OPTIMIZER_DATABASE_DSN is not set; refusing to run migrations.",
            file=sys.stderr,
        )
        return 1

    applied = apply_migrations(dsn)
    if not applied:
        print("No new migrations to apply; schema is up to date.")
        return 0

    print(f"Applied {len(applied)} migration(s):")
    for filename in applied:
        print(f"  - {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
