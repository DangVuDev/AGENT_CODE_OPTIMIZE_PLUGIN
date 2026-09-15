# Model Provider Resilience — Safe Rollout Plan

## Decision

Model calls are external, retryable side effects. Provider unavailability must
not be confused with invalid business output. Resilience is introduced in
small, independently releasable phases; LangGraph topology is unchanged until
durable checkpoint/resume is available.

## Phase 1 — bounded transport resilience

1. Classify retryable status codes and provider SDK exceptions centrally.
2. Honour a valid `Retry-After` header.
3. Otherwise use exponential **equal jitter**, which guarantees a meaningful
   minimum wait while preventing synchronized retry storms.
4. Enforce both maximum attempts and maximum elapsed retry delay.
5. Let the final provider exception reach the CLI, where it is rendered as a
   concise controlled failure with a non-zero exit code.
6. Never retry authentication, authorization, malformed request, safety or
   schema errors as transport failures.

Default schedule with `base=2s`, `cap=30s`:

```text
after attempt 1: 1–2 seconds
after attempt 2: 2–4 seconds
after attempt 3: 4–8 seconds
```

Acceptance criteria:

- no computed delay is below half of its exponential ceiling;
- `Retry-After` wins but is capped;
- retry stops before exceeding the elapsed-delay budget;
- permanent errors are raised immediately;
- successful retry returns the provider result unchanged.

## Phase 2 — resilient gateway and approved fallback (implemented)

`select_model_provider()` now returns a `ResilientModelGateway` for real model
providers. Each configured provider is wrapped with Phase 1 retry/backoff, then
the gateway applies:

1. An ordered approved provider chain from `OPTIMIZER_MODEL_PROVIDER_ORDER`
   (`anthropic,openai,gemini,deepseek,ollama` by default).
2. A circuit breaker per provider/model after an exhausted retryable failure.
3. Fallback to the next approved provider only for retryable transport errors.
4. Immediate failure for permanent errors such as invalid credentials or
   malformed requests.
5. A typed `DeferredModelCallError` when no approved provider can serve the
   call.

The gateway rewrites the request's `model_id` for the provider actually being
called, while preserving the original node idempotency key. CLI/UI entrypoints
render `DeferredModelCallError` as a clean retry-later halt instead of a raw SDK
traceback.

Acceptance criteria:

- transient primary failure can fall back to the next approved provider;
- open circuits are skipped until cooldown expires;
- cooldown expiry allows the same provider to be retried;
- permanent errors do not fall back to another provider;
- no fallback ever uses `LocalScriptedModelProvider` as a fake production
  substitute.

## Phase 3 — durable defer/resume substrate (implemented)

The control-plane substrate now exists:

1. `DeferredModelCallRecord` and `ModelCallFailureRecord` contracts capture the
   retry-later state without mixing provider outages into A3/S02 business
   artifacts.
2. `ModelCallDeferralPort` defines `defer`, `claim_due`, `mark_succeeded` and
   `mark_failed`.
3. `PostgresModelCallDeferralStore` persists records in
   `optimizer_control.model_call_deferrals`.
4. Migration `003_model_call_deferrals.sql` creates the table, idempotency
   index and due-claim index.
5. `ResilientModelGateway` persists a deferred record before raising
   `DeferredModelCallError` when all approved providers are unavailable.
6. Local CLI/control-plane runs use `MemoryModelCallDeferralStore`; durable mode
   requires Postgres for deferrals as part of the durability posture.
7. A3 and S02 graph builders accept an optional LangGraph checkpointer, and the
   CLI/UI/export entrypoints pass `thread_id` into graph invocation when the
   durable checkpointer is available.
8. `ModelDeferralScheduler` claims due records, resumes the checkpointed A3 or
   S02 graph by `thread_id`, and marks records `succeeded` or `failed`.
9. `scripts/run_model_deferral_scheduler.py` runs the scheduler as a one-shot or
   polling worker.

Remaining production work:

- add an integration test that exercises a real Postgres checkpointer plus
  `model_call_deferrals` table together;
- expose admin/status APIs for deferred records.

## Remaining non-goals

- sleeping for delayed retries inside a worker;
- silently switching to an unconfigured provider;
- returning a fabricated model result;
- swallowing the final error; or
- adding a LangGraph route that cannot yet be durably resumed.
