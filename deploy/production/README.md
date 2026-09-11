# Production deployment placeholder

No production deployment is provided by the scaffold.

Before adding manifests, complete the component ADRs and pin every image by
immutable digest. Production requires separate least-privilege database roles,
PostgreSQL-backed LangGraph checkpoint setup, encrypted object storage with
versioning/retention, KMS-backed signing, identity/RBAC, policy bundles,
short-lived secrets, isolated workers, network policy, quotas, telemetry,
backup/restore and operational runbooks.

Development Compose tags and credentials must never be promoted into this
directory.

