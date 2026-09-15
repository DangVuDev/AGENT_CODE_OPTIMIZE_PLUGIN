package checkout_test

import (
	"context"
	"testing"

	application "example.com/go-checkout/internal/application/checkout"
	"example.com/go-checkout/internal/repository/memory"
)

func TestCheckoutIsIdempotent(t *testing.T) {
	store := memory.NewStore()
	service := application.NewService(store, store, store, store)
	command := application.Command{CustomerID: "customer-1", Currency: "USD", CouponCode: "SAVE10", IdempotencyKey: "request-1", Items: []application.ItemInput{{SKU: "coffee", Quantity: 2}, {SKU: "mug", Quantity: 1}}}
	first, replayed, err := service.Checkout(context.Background(), command)
	if err != nil || replayed {
		t.Fatalf("first checkout: replayed=%v err=%v", replayed, err)
	}
	second, replayed, err := service.Checkout(context.Background(), command)
	if err != nil || !replayed {
		t.Fatalf("second checkout: replayed=%v err=%v", replayed, err)
	}
	if first.ID != second.ID || first.TotalCents != 2610 {
		t.Fatalf("unexpected orders: first=%+v second=%+v", first, second)
	}
}
