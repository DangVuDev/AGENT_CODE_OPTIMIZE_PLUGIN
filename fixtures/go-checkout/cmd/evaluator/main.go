package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"time"
)

type checkoutResponse struct {
	TotalCents int64 `json:"total_cents"`
}

func main() {
	const repetitions = 40
	payload := []byte(`{"customer_id":"evaluator","currency":"USD","coupon_code":"SAVE10","items":[{"sku":"coffee","quantity":2},{"sku":"mug","quantity":1}]}`)
	client := &http.Client{Timeout: 3 * time.Second}
	durations := make([]float64, 0, repetitions)
	correctness := 0
	for range repetitions {
		started := time.Now()
		request, err := http.NewRequest(http.MethodPost, "http://127.0.0.1:8080/v1/checkouts", bytes.NewReader(payload))
		if err != nil {
			fail(err)
		}
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Idempotency-Key", fmt.Sprintf("evaluation-%d-%d", os.Getpid(), time.Now().UnixNano()))
		response, err := client.Do(request)
		if err != nil {
			fail(err)
		}
		body, err := io.ReadAll(response.Body)
		response.Body.Close()
		if err != nil || response.StatusCode != http.StatusCreated {
			fail(fmt.Errorf("checkout failed: status=%d body=%s", response.StatusCode, body))
		}
		var output checkoutResponse
		if err := json.Unmarshal(body, &output); err != nil {
			fail(err)
		}
		if output.TotalCents != 2610 {
			correctness = 1
		}
		durations = append(durations, float64(time.Since(started).Microseconds())/1000.0)
	}
	sort.Float64s(durations)
	p95 := durations[(len(durations)*95+99)/100-1]
	result := map[string]any{
		"schema_version": "1.0",
		"feature_id":     "checkout",
		"metrics": map[string]any{
			"p95_latency_ms": p95,
			"correctness":    correctness,
		},
		"samples":  map[string]any{"latency_ms": durations},
		"metadata": map[string]any{"requests_per_evaluation": repetitions, "language": "go"},
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fail(err)
	}
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
