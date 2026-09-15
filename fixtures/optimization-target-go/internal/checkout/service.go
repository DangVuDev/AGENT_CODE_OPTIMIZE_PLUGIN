package checkout

import (
	"context"
	"errors"
	"fmt"
)

var ErrProductNotFound = errors.New("product not found")

type Item struct {
	SKU      string `json:"sku"`
	Quantity int64  `json:"quantity"`
}

type Product struct {
	SKU        string
	PriceCents int64
}

type Catalog interface {
	FindProduct(context.Context, string) (Product, error)
}

type Request struct {
	Items []Item `json:"items"`
}

type Result struct {
	SubtotalCents int64 `json:"subtotal_cents"`
	ItemCount     int64 `json:"item_count"`
}

type Service struct{ catalog Catalog }

func NewService(catalog Catalog) *Service { return &Service{catalog: catalog} }

func (service *Service) Calculate(ctx context.Context, request Request) (Result, error) {
	if len(request.Items) == 0 {
		return Result{}, errors.New("at least one item is required")
	}
	var result Result
	for _, item := range request.Items {
		if item.SKU == "" || item.Quantity <= 0 {
			return Result{}, errors.New("SKU and positive quantity are required")
		}
		product, err := service.catalog.FindProduct(ctx, item.SKU)
		if err != nil {
			return Result{}, fmt.Errorf("%w: %s", ErrProductNotFound, item.SKU)
		}
		result.SubtotalCents += product.PriceCents * item.Quantity
		result.ItemCount += item.Quantity
	}
	return result, nil
}
