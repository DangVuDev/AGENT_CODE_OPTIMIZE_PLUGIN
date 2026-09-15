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

const repetitions = 60

type checkoutResult struct {
	SubtotalCents int64 `json:"subtotal_cents"`
	ItemCount     int64 `json:"item_count"`
}

type evaluationOutput struct {
	SchemaVersion string                 `json:"schema_version"`
	FeatureID     string                 `json:"feature_id"`
	Metrics       map[string]any         `json:"metrics"`
	Samples       map[string][]float64   `json:"samples"`
	Metadata      map[string]interface{} `json:"metadata"`
}

func main() {
	payload := buildPayload()
	client := &http.Client{Timeout: 5 * time.Second}
	durations := make([]float64, 0, repetitions)
	correct := true
	for index := 0; index < repetitions; index++ {
		started := time.Now()
		response, err := client.Post(
			"http://127.0.0.1:8080/v1/checkout/calculate",
			"application/json",
			bytes.NewReader(payload),
		)
		if err != nil {
			fail(err)
		}
		body, readErr := io.ReadAll(response.Body)
		response.Body.Close()
		if readErr != nil || response.StatusCode != http.StatusOK {
			fail(fmt.Errorf("request failed: status=%d body=%s", response.StatusCode, body))
		}
		var result checkoutResult
		if err := json.Unmarshal(body, &result); err != nil {
			fail(err)
		}
		correct = correct && result.ItemCount == 40 && result.SubtotalCents == 23580
		durations = append(durations, float64(time.Since(started).Microseconds())/1000)
	}
	sort.Float64s(durations)
	p95 := durations[(len(durations)*95+99)/100-1]
	output := evaluationOutput{
		SchemaVersion: "1.0",
		FeatureID:     "checkout-calculation",
		Metrics: map[string]any{
			"p95_latency_ms": p95,
			"throughput_rps": 1000 / average(durations),
			"correctness":    correct,
		},
		Samples:  map[string][]float64{"latency_ms": durations},
		Metadata: map[string]interface{}{"requests": repetitions, "catalog_size": 50000},
	}
	if err := json.NewEncoder(os.Stdout).Encode(output); err != nil {
		fail(err)
	}
}

func buildPayload() []byte {
	items := make([]map[string]any, 0, 20)
	for index := 49980; index < 50000; index++ {
		items = append(items, map[string]any{"sku": fmt.Sprintf("sku-%d", index), "quantity": 2})
	}
	payload, err := json.Marshal(map[string]any{"items": items})
	if err != nil {
		fail(err)
	}
	return payload
}

func average(values []float64) float64 {
	var total float64
	for _, value := range values {
		total += value
	}
	return total / float64(len(values))
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
