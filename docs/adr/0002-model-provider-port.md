# ADR-0002: Model Provider Port

Status: accepted, amended
Scope: unblock A3.40 (generate finding drafts) and A3.50 (independent judge);
amendment extends adapter coverage to OpenAI, DeepSeek, a local/self-hosted
OpenAI-compatible runtime (Ollama, vLLM, LM Studio, ...) and Gemini
Code status: port + five concrete adapters (Anthropic, OpenAI, DeepSeek,
Ollama, Gemini); A3 handlers built on top of this port are covered
separately, not by this document

## Context

A3's playbook (`docs/implementation/05-lane-1-detailed-implementation-
playbook.md` §"A3 implementation order by node") requires two genuinely
generative steps: A3.40 drafts typed findings from evidence, and A3.50 runs
an independent judge over each draft. Both need a real language model call.
No such capability exists anywhere in the platform today — `NodePorts`
(`application/node_runtime.py`) had no model-call port, and no LLM SDK was a
project dependency.

Two decisions were made with the user before this ADR (not re-litigated
here): build the full A3 lane now rather than deferring the LLM-dependent
half, and make the port provider-agnostic rather than committing directly to
one vendor's SDK types.

## Decision

### 1. Provider-agnostic `ModelProviderPort`

`ports/model_provider.py`:
```python
class ModelProviderPort(Protocol):
    def complete(self, request: ModelCompletionRequest) -> ModelCompletionResult: ...
    def healthcheck(self) -> bool: ...
```
backed by new `contracts/platform.py` types (`ModelRole`, `ModelMessage`,
`ModelCompletionRequest`, `ModelCompletionResult`) — no Anthropic/OpenAI
type crosses this boundary, matching how `PolicyRequest`/`WorkerJob` already
keep `PolicyPort`/`WorkerBroker` vendor-neutral.

`ModelCompletionRequest.role: ModelRole` (`GENERATOR` | `JUDGE`) is the
mechanism, not just a comment, behind the playbook's "generator cannot judge
itself": a judge call is a structurally separate request with its own
message list — there is no API for continuing a generator's conversation
into a judge call. `NodePorts.model: ModelProviderPort | None` follows the
existing optional-with-explicit-`None`-check pattern from ADR-0001.

Two things are deliberately **not** part of the port, and stay in the
calling node instead:
- **The one-bounded-repair-on-invalid-JSON pattern** (A3.40's playbook
  requirement) — the port just reports `valid_json`/`parsed_json`; A3.40
  decides whether and how to retry.
- **Token-budget enforcement** against `ExecutionBudget.maximum_model_tokens`
  — the port reports `input_tokens`/`output_tokens` per call; the caller
  (A3.60/A3.82's revision-loop controller) accumulates and checks.

### 2. First (only, for now) adapter: Anthropic

`adapters/production/anthropic_model_provider.py`, `AnthropicModelProvider`:
- New dependency: `anthropic==0.75.0` in `pyproject.toml`'s `production`
  group.
- The `anthropic` import is **lazy** (inside `complete()`/`healthcheck()`,
  not at module top level), so importing this module — and therefore
  `adapters.production.__init__` — never fails in an environment that
  hasn't installed the `production` dependency group, matching how every
  other prod-only adapter in this package is already isolated.
- The API key is leased per call via `SecretsBroker.lease(tenant_id=...,
  secret_ref=..., purpose=f"a3-model-completion:{role}", ttl_seconds=60)` —
  never read from an environment variable or cached on the instance.
  `SecretsBroker` was already on `NodePorts` (ADR-0001); this is its first
  real consumer.
- Structured output is forced via Anthropic's tool-use mechanism:
  `request.response_schema` becomes the one tool's `input_schema`, and
  `tool_choice` pins the model to call exactly that tool. A reply that
  doesn't produce that tool call (e.g. plain text, refusal) yields
  `parsed_json=None`, `valid_json=False` — a real signal, not a guess from
  text parsing.
- Judge calls use `temperature=0`; generator calls use a low nonzero
  temperature (`0.2`) — the judge, not the generator, is the
  deterministic-leaning check, per the playbook's framing.

### 3. Test strategy

- Unit/contract tests use a scripted fake `ModelProviderPort` (same pattern
  as `_AllowPolicy`/`_AcceptingWorkerBroker` in
  `test_a2_production_handlers.py`) — no network call, no cost, tests A3
  handler *logic* (context building, repair-on-invalid-JSON, routing).
- `tests/integration/test_anthropic_model_provider.py` makes one real API
  call and is skipped whenever `ANTHROPIC_API_KEY` is unset — mirrors
  `_require_database(dsn)`'s skip-if-unreachable pattern already used for
  Postgres/S3/etc. Never required for the default `pytest -q` run.

### 4. Amendment: OpenAI, DeepSeek, Ollama/local and Gemini adapters

Requested by the user after the initial (Anthropic-only) version of this
ADR shipped: broaden vendor coverage so a case is never locked to one LLM
provider. Confirmed with the user before implementing: **one class per
vendor** (not one parameterized `OpenAICompatibleModelProvider` config'd by
`base_url`), because each vendor's credential/config story is managed
independently even where the wire protocol is shared.

- **`adapters/production/openai_model_provider.py`,
  `OpenAIModelProvider`** — OpenAI Chat Completions API via the `openai`
  SDK (new dependency, `openai==2.15.0`). Structured output via the same
  tool-call pattern as `AnthropicModelProvider` (one `emit_result` function
  tool, `tool_choice` pinned to it), so both adapters give identical
  `valid_json`/`parsed_json` semantics to a calling node.
- **`adapters/production/deepseek_model_provider.py`,
  `DeepSeekModelProvider`** — DeepSeek's API is wire-compatible with
  OpenAI's, so this also uses the `openai` SDK, pointed at
  `https://api.deepseek.com` by default (overridable). Still a separate
  public class from `OpenAIModelProvider` per the "one class per vendor"
  decision above, even though the request-building logic is identical.
- **`adapters/production/ollama_model_provider.py`,
  `OllamaModelProvider`** — same OpenAI-compatible wire protocol again, for
  a local/self-hosted server (Ollama's own default port, or any other
  runtime speaking the same protocol — vLLM, LM Studio, llama.cpp server's
  `--api` mode). The one adapter here where `secrets` is **optional**: a
  local server usually has no real credential to lease, so this defaults to
  Ollama's own documented placeholder API key string instead of forcing a
  `SecretsBroker` a local-only deployment may not have configured. If
  `secrets`/`secret_ref` *are* supplied (e.g. an authenticating proxy in
  front of a shared local server), they are leased exactly like the hosted
  adapters.
- **`adapters/production/gemini_model_provider.py`,
  `GeminiModelProvider`** — Google's Gemini API via the `google-genai` SDK
  (new dependency, `google-genai==1.51.0`; distinct from the deprecated
  `google-generativeai` package). Uses Gemini's native
  `response_mime_type="application/json"` + `response_json_schema` config
  instead of tool-calling — Gemini supports schema-constrained JSON output
  directly, so no synthetic `emit_result` tool wrapper is needed there.
- **`adapters/production/_openai_compatible.py`** — the one piece of code
  actually shared across the three OpenAI-wire-protocol adapters
  (`complete_via_openai_chat`), so the request/response translation isn't
  copy-pasted three times even though the three public classes stay
  separate. Not itself a `ModelProviderPort` implementation and not
  exported from `adapters.production`.

**Validates the original design**: none of this needed a single change to
`ports/model_provider.py`, `contracts/platform.py`, `NodePorts`, or any A3
handler — exactly the outcome §"Consequences" below predicted when the port
was first built as provider-agnostic.

**Test strategy for the amendment**: same shape as Anthropic's —
`tests/integration/test_openai_compatible_model_providers.py` covers
OpenAI/DeepSeek (skip if `OPENAI_API_KEY`/`DEEPSEEK_API_KEY` unset) and
Ollama (skip if nothing is listening on the configured local port — no API
key needed, so this one is free to run whenever a local server exists);
`tests/integration/test_gemini_model_provider.py` covers Gemini (skip if
`GEMINI_API_KEY` unset). None run as part of the default `pytest -q` suite.

## Consequences

- A3.40/A3.50 (and everything downstream that depends on their output —
  effectively the rest of A3) can now be implemented against a real port
  instead of staying blocked.
- Anthropic is not a permanent lock-in: any other vendor SDK can implement
  `ModelProviderPort` without touching a single A3 handler, since handlers
  only ever see `ModelCompletionRequest`/`ModelCompletionResult`.
- Real API cost is opt-in and observable: it only happens when a caller
  constructs `AnthropicModelProvider` with real `SecretsBroker` credentials
  and `ANTHROPIC_API_KEY` is actually set for the one smoke test that uses
  it.
- No existing test, adapter, or handler changes behavior — `NodePorts.model`
  is a new optional field, `contracts/platform.py`'s additions are pure
  additions, and `adapters.production.__init__`'s new import is lazy.

## Alternatives considered

- **Commit directly to the Anthropic SDK's types in A3 handlers** (skip the
  port). Rejected — identical reasoning to why `PolicyPort`/`WorkerBroker`
  exist at all: it would make every A3.40/A3.50/A3.60 handler untestable
  without a real API key and hard to swap vendors later.
- **A full multi-vendor abstraction layer up front** (e.g. LiteLLM-style
  routing, retries, fallback config). Rejected for now — one concrete
  adapter behind a clean `Protocol` already gives vendor independence at
  the type level; a routing/fallback layer can be added later as a second
  adapter or a composing adapter without changing the port shape.
- **Read the API key from an environment variable inside the adapter.**
  Rejected — violates the project's existing rule (CLAUDE.md: "Never store
  credentials in checkpoint state, telemetry events, artifact metadata")
  and the precedent every other credentialed adapter follows via
  `SecretsBroker`.
