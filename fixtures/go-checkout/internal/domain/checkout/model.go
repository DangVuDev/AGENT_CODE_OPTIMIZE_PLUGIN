package checkout

import (
	"errors"
	"fmt"
	"time"
)

var (
	ErrEmptyCart           = errors.New("cart must contain at least one item")
	ErrInvalidQuantity     = errors.New("quantity must be positive")
	ErrInvalidUnitPrice    = errors.New("unit price must be non-negative")
	ErrInvalidDiscount     = errors.New("discount must be between 0 and 10000 basis points")
	ErrUnsupportedCurrency = errors.New("unsupported currency")
)

type Line struct {
	SKU            string `json:"sku"`
	Name           string `json:"name"`
	Quantity       int64  `json:"quantity"`
	UnitPriceCents int64  `json:"unit_price_cents"`
	LineTotalCents int64  `json:"line_total_cents"`
}

type Order struct {
	ID                  string    `json:"id"`
	CustomerID          string    `json:"customer_id"`
	Currency            string    `json:"currency"`
	Lines               []Line    `json:"lines"`
	SubtotalCents       int64     `json:"subtotal_cents"`
	DiscountCents       int64     `json:"discount_cents"`
	TotalCents          int64     `json:"total_cents"`
	DiscountBasisPoints int64     `json:"discount_basis_points"`
	CreatedAt           time.Time `json:"created_at"`
}

func Price(lines []Line, currency string, discountBasisPoints int64) (Order, error) {
	if len(lines) == 0 {
		return Order{}, ErrEmptyCart
	}
	if currency != "USD" {
		return Order{}, fmt.Errorf("%w: %s", ErrUnsupportedCurrency, currency)
	}
	if discountBasisPoints < 0 || discountBasisPoints > 10_000 {
		return Order{}, ErrInvalidDiscount
	}
	priced := make([]Line, len(lines))
	var subtotal int64
	for index, line := range lines {
		if line.SKU == "" {
			return Order{}, errors.New("SKU is required")
		}
		if line.Quantity <= 0 {
			return Order{}, fmt.Errorf("%w for SKU %s", ErrInvalidQuantity, line.SKU)
		}
		if line.UnitPriceCents < 0 {
			return Order{}, fmt.Errorf("%w for SKU %s", ErrInvalidUnitPrice, line.SKU)
		}
		line.LineTotalCents = line.UnitPriceCents * line.Quantity
		priced[index] = line
		subtotal += line.LineTotalCents
	}
	discount := subtotal * discountBasisPoints / 10_000
	return Order{Currency: currency, Lines: priced, SubtotalCents: subtotal, DiscountCents: discount, TotalCents: subtotal - discount, DiscountBasisPoints: discountBasisPoints}, nil
}
