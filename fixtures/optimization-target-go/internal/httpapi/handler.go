package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"

	"example.com/optimization-target/internal/checkout"
)

type Handler struct{ service *checkout.Service }

func New(service *checkout.Service) http.Handler {
	handler := &Handler{service: service}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health/ready", handler.ready)
	mux.HandleFunc("POST /v1/checkout/calculate", handler.calculate)
	return mux
}

func (handler *Handler) ready(response http.ResponseWriter, _ *http.Request) {
	response.WriteHeader(http.StatusNoContent)
}

func (handler *Handler) calculate(response http.ResponseWriter, request *http.Request) {
	request.Body = http.MaxBytesReader(response, request.Body, 1<<20)
	var input checkout.Request
	decoder := json.NewDecoder(request.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		http.Error(response, "invalid JSON", http.StatusBadRequest)
		return
	}
	result, err := handler.service.Calculate(request.Context(), input)
	if err != nil {
		status := http.StatusUnprocessableEntity
		if errors.Is(err, checkout.ErrProductNotFound) {
			status = http.StatusNotFound
		}
		http.Error(response, err.Error(), status)
		return
	}
	response.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(response).Encode(result)
}
