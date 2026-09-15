package catalog_test

import (
	"context"
	"testing"

	"example.com/optimization-target/internal/catalog"
)

func TestCatalogFindsFirstAndLastProduct(t *testing.T) {
	products := catalog.New(50000)
	for _, sku := range []string{"sku-0", "sku-49999"} {
		product, err := products.FindProduct(context.Background(), sku)
		if err != nil || product.SKU != sku {
			t.Fatalf("FindProduct(%q) = %+v, %v", sku, product, err)
		}
	}
}
