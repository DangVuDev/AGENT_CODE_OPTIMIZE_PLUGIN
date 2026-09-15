package catalog

import (
	"context"
	"strconv"

	"example.com/optimization-target/internal/checkout"
)

// Catalog deliberately stores products as a slice. FindProduct performs a
// linear scan, making checkout O(items * catalog_size). This is valid but
// inefficient production-style code for the optimizer to discover and fix.
type Catalog struct{ products []checkout.Product }

func New(size int) *Catalog {
	products := make([]checkout.Product, 0, size)
	for index := 0; index < size; index++ {
		products = append(products, checkout.Product{
			SKU: "sku-" + strconv.Itoa(index), PriceCents: int64(100 + index%900),
		})
	}
	return &Catalog{products: products}
}

func (catalog *Catalog) FindProduct(ctx context.Context, sku string) (checkout.Product, error) {
	for _, product := range catalog.products {
		select {
		case <-ctx.Done():
			return checkout.Product{}, ctx.Err()
		default:
		}
		if product.SKU == sku {
			return product, nil
		}
	}
	return checkout.Product{}, checkout.ErrProductNotFound
}
