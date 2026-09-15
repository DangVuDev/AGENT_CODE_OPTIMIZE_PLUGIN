# Optimization Target: Go Checkout

This repository fixture is a realistic black-box optimization target for Lane 1.
It is intentionally correct but inefficient: checkout resolves every line item
by scanning a 50,000-product catalog. A request containing 20 late-catalog SKUs
therefore performs roughly one million comparisons.

## Production shape

```text
cmd/server             HTTP process and graceful shutdown
cmd/evaluator          black-box feature workload and metric emitter
internal/checkout      use case and catalog port
internal/catalog       deliberately slow catalog adapter
internal/httpapi       HTTP transport and validation
scripts/               stable evaluator entrypoint consumed by A2
compose.yaml           isolated application lifecycle
optimization-request.json  example A1 input values
```

The optimization target is `Catalog.FindProduct`. A valid candidate must reduce
checkout latency without changing prices, quantities, status codes, or missing
product behavior. The expected design improvement is an indexed lookup, but the
fixture does not require any particular implementation.

## Local verification

```sh
go test ./...
docker compose up --build -d --wait
docker compose exec -T app sh scripts/evaluate-checkout.sh
docker compose down --volumes --remove-orphans
```

The evaluator prints exactly one `EvaluationOutput` JSON document. It measures
`p95_latency_ms`, derives `throughput_rps`, validates `correctness`, and includes
all raw latency samples. A2 can execute the same command repeatedly from the
declaration in `optimization-request.json`.

Expected baseline characteristics vary by host, but latency should be
substantially worse than a map-indexed implementation. The correctness metric
must always remain `true`.
