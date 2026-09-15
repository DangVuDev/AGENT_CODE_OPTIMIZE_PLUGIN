package checkout_test

import (
	"context"
	"errors"
	"testing"

	"example.com/optimization-target/internal/checkout"
)

type catalogStub map[string]checkout.Product

func (catalog catalogStub) FindProduct(_ context.Context, sku string) (checkout.Product, error) {
	product, found := catalog[sku]
	if !found {
		return checkout.Product{}, checkout.ErrProductNotFound
	}
	return product, nil
}

func TestCalculatePreservesPriceAndQuantity(t *testing.T) {
	service := checkout.NewService(catalogStub{
		"coffee": {SKU: "coffee", PriceCents: 1200},
		"mug":    {SKU: "mug", PriceCents: 500},
	})
	result, err := service.Calculate(context.Background(), checkout.Request{Items: []checkout.Item{
		{SKU: "coffee", Quantity: 2}, {SKU: "mug", Quantity: 1},
	}})
	if err != nil {
		t.Fatalf("Calculate returned an error: %v", err)
	}
	if result.SubtotalCents != 2900 || result.ItemCount != 3 {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestCalculateRejectsUnknownProduct(t *testing.T) {
	service := checkout.NewService(catalogStub{})
	_, err := service.Calculate(context.Background(), checkout.Request{
		Items: []checkout.Item{{SKU: "missing", Quantity: 1}},
	})
	if !errors.Is(err, checkout.ErrProductNotFound) {
		t.Fatalf("expected ErrProductNotFound, got %v", err)
	}
}
