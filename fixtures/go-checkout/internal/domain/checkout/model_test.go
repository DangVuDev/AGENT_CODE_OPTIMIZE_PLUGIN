package checkout

import (
	"errors"
	"testing"
)

func TestPriceAppliesDiscountWithoutFloatingPoint(t *testing.T) {
	order, err := Price([]Line{{SKU: "coffee", Quantity: 2, UnitPriceCents: 1200}, {SKU: "mug", Quantity: 1, UnitPriceCents: 500}}, "USD", 1000)
	if err != nil {
		t.Fatal(err)
	}
	if order.SubtotalCents != 2900 || order.DiscountCents != 290 || order.TotalCents != 2610 {
		t.Fatalf("unexpected totals: %+v", order)
	}
}

func TestPriceRejectsInvalidInput(t *testing.T) {
	tests := []struct {
		name     string
		lines    []Line
		currency string
		discount int64
		want     error
	}{
		{"empty cart", nil, "USD", 0, ErrEmptyCart},
		{"zero quantity", []Line{{SKU: "coffee", Quantity: 0}}, "USD", 0, ErrInvalidQuantity},
		{"unsupported currency", []Line{{SKU: "coffee", Quantity: 1}}, "EUR", 0, ErrUnsupportedCurrency},
		{"invalid discount", []Line{{SKU: "coffee", Quantity: 1}}, "USD", 10001, ErrInvalidDiscount},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			_, err := Price(test.lines, test.currency, test.discount)
			if !errors.Is(err, test.want) {
				t.Fatalf("expected %v, got %v", test.want, err)
			}
		})
	}
}
