package httpapi

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	application "example.com/go-checkout/internal/application/checkout"
)

type Handler struct {
	service            *application.Service
	logger             *slog.Logger
	requests, failures atomic.Uint64
}

func New(service *application.Service, logger *slog.Logger) http.Handler {
	handler := &Handler{service: service, logger: logger}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/live", handler.live)
	mux.HandleFunc("GET /health/ready", handler.ready)
	mux.HandleFunc("GET /metrics", handler.metrics)
	mux.HandleFunc("POST /v1/checkouts", handler.checkout)
	mux.HandleFunc("GET /v1/orders/{id}", handler.getOrder)
	return handler.middleware(mux)
}
func (handler *Handler) middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		started := time.Now()
		handler.requests.Add(1)
		writer.Header().Set("X-Content-Type-Options", "nosniff")
		next.ServeHTTP(writer, request)
		handler.logger.Info("request", "method", request.Method, "path", request.URL.Path, "duration_us", time.Since(started).Microseconds())
	})
}
func (*Handler) live(writer http.ResponseWriter, _ *http.Request) {
	writer.WriteHeader(http.StatusNoContent)
}
func (*Handler) ready(writer http.ResponseWriter, _ *http.Request) {
	writer.WriteHeader(http.StatusNoContent)
}
func (handler *Handler) metrics(writer http.ResponseWriter, _ *http.Request) {
	writeJSON(writer, http.StatusOK, map[string]uint64{"http_requests_total": handler.requests.Load(), "http_failures_total": handler.failures.Load()})
}
func (handler *Handler) checkout(writer http.ResponseWriter, request *http.Request) {
	var command application.Command
	decoder := json.NewDecoder(http.MaxBytesReader(writer, request.Body, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&command); err != nil {
		handler.problem(writer, http.StatusBadRequest, "invalid_request", err)
		return
	}
	command.IdempotencyKey = request.Header.Get("Idempotency-Key")
	order, replayed, err := handler.service.Checkout(request.Context(), command)
	if err != nil {
		handler.problem(writer, statusFor(err), "checkout_failed", err)
		return
	}
	if replayed {
		writer.Header().Set("Idempotent-Replayed", "true")
	}
	writeJSON(writer, http.StatusCreated, order)
}
func (handler *Handler) getOrder(writer http.ResponseWriter, request *http.Request) {
	order, err := handler.service.GetOrder(request.Context(), request.PathValue("id"))
	if err != nil {
		handler.problem(writer, statusFor(err), "order_lookup_failed", err)
		return
	}
	writeJSON(writer, http.StatusOK, order)
}
func (handler *Handler) problem(writer http.ResponseWriter, status int, code string, err error) {
	handler.failures.Add(1)
	writeJSON(writer, status, map[string]string{"code": code, "message": err.Error()})
}
func statusFor(err error) int {
	if errors.Is(err, application.ErrOrderNotFound) || errors.Is(err, application.ErrProductNotFound) {
		return http.StatusNotFound
	}
	if errors.Is(err, application.ErrInsufficientStock) || strings.Contains(err.Error(), "coupon") {
		return http.StatusUnprocessableEntity
	}
	return http.StatusBadRequest
}
func writeJSON(writer http.ResponseWriter, status int, value any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(value)
}
