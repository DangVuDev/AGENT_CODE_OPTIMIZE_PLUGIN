package httpapi_test

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"

	application "example.com/go-checkout/internal/application/checkout"
	"example.com/go-checkout/internal/repository/memory"
	"example.com/go-checkout/internal/transport/httpapi"
)

func TestCheckoutEndpoint(t *testing.T) {
	store := memory.NewStore()
	service := application.NewService(store, store, store, store)
	server := httptest.NewServer(httpapi.New(service, slog.New(slog.NewTextHandler(io.Discard, nil))))
	defer server.Close()
	body := []byte(`{"customer_id":"customer-1","currency":"USD","coupon_code":"SAVE10","items":[{"sku":"coffee","quantity":2},{"sku":"mug","quantity":1}]}`)
	request, _ := http.NewRequest(http.MethodPost, server.URL+"/v1/checkouts", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Idempotency-Key", "integration-1")
	response, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusCreated {
		t.Fatalf("expected 201, got %d", response.StatusCode)
	}
	var result struct {
		TotalCents int64 `json:"total_cents"`
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		t.Fatal(err)
	}
	if result.TotalCents != 2610 {
		t.Fatalf("expected 2610, got %d", result.TotalCents)
	}
}
