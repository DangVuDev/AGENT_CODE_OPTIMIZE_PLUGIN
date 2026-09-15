FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go test ./... \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/server ./cmd/server \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/evaluator ./cmd/evaluator

FROM alpine:3.21
RUN addgroup -S app && adduser -S -G app app
WORKDIR /app
COPY --from=build /out/server /app/server
COPY --from=build /out/evaluator /app/evaluator
COPY scripts /app/scripts
RUN chmod 0555 /app/server /app/evaluator /app/scripts/evaluate-checkout.sh
USER app
EXPOSE 8080
ENTRYPOINT ["/app/server"]
