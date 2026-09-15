.PHONY: test run evaluate compose-up compose-down

test:
	go test -cover ./...

race:
	CGO_ENABLED=1 go test -race ./...

run:
	go run ./cmd/server

evaluate:
	go run ./cmd/evaluator

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down --volumes --remove-orphans
