# Security Policy

Report vulnerabilities through the organization's private security channel.
Do not open public issues containing source, evidence, credentials, tenant IDs,
or exploit details.

This scaffold contains no production secret. `.env.example` values are local
development placeholders. Target repositories, source files, raw observations,
model transcripts and credentials must never be placed in LangGraph checkpoint
state or ordinary application logs.

Production enablement requires the threat model, tenant-isolation tests,
short-lived secret broker, immutable artifact storage, policy/RBAC integration,
and isolated worker controls described in the blueprint.

