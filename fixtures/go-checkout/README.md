# Go Checkout Service

This fixture is a small but complete Go HTTP service used to exercise the optimizer's A1 and A2 stages against a non-Python codebase.

## Architecture

```text
cmd/server                         composition root and graceful shutdown
cmd/evaluator                      black-box product evaluator
internal/domain/checkout           pricing rules and domain entities
internal/application/checkout      checkout use case and ports
internal/repository/memory         thread-safe catalog/order adapter
internal/transport/httpapi         HTTP API, validation and middleware
internal/config                    environment configuration
scripts/evaluate-checkout.sh       stable A2 evaluator entrypoint
```

The application layer depends on interfaces, not the in-memory adapter. Monetary calculations use integer cents. Checkout creation requires an idempotency key and supports replay-safe requests.

## API

- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`
- `POST /v1/checkouts`
- `GET /v1/orders/{id}`

Example request:

```json
{
  "customer_id": "customer-1",
  "currency": "USD",
  "coupon_code": "SAVE10",
  "items": [
    {"sku": "coffee", "quantity": 2},
    {"sku": "mug", "quantity": 1}
  ]
}
```

It produces a subtotal of 2900 cents, a 290-cent discount and a final total of 2610 cents.

## Verification

```sh
go test -cover ./...
docker compose up --build -d --wait
docker compose exec -T app sh /app/scripts/evaluate-checkout.sh
docker compose down --volumes --remove-orphans
```

The evaluator performs real HTTP requests, validates the business result and emits the strict JSON protocol consumed by A2:

```json
{
  "schema_version": "1.0",
  "feature_id": "checkout",
  "metrics": {
    "p95_latency_ms": 1.5,
    "correctness": 0
  }
}
```
