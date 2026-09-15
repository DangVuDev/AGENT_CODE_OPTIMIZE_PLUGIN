package memory

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"

	application "example.com/go-checkout/internal/application/checkout"
	domain "example.com/go-checkout/internal/domain/checkout"
)

type Store struct {
	mu          sync.RWMutex
	products    map[string]application.Product
	orders      map[string]domain.Order
	idempotency map[string]string
	sequence    atomic.Uint64
}

func NewStore() *Store {
	return &Store{products: map[string]application.Product{"coffee": {SKU: "coffee", Name: "Single-origin coffee", PriceCents: 1200, Available: 10000}, "mug": {SKU: "mug", Name: "Ceramic mug", PriceCents: 500, Available: 10000}}, orders: make(map[string]domain.Order), idempotency: make(map[string]string)}
}
func (store *Store) FindProduct(_ context.Context, sku string) (application.Product, error) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	product, ok := store.products[sku]
	if !ok {
		return application.Product{}, errors.New("missing product")
	}
	return product, nil
}
func (*Store) DiscountBasisPoints(_ context.Context, code string) (int64, error) {
	switch code {
	case "":
		return 0, nil
	case "SAVE10":
		return 1000, nil
	default:
		return 0, errors.New("invalid coupon")
	}
}
func (store *Store) FindByIdempotencyKey(_ context.Context, key string) (domain.Order, bool, error) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	id, ok := store.idempotency[key]
	if !ok {
		return domain.Order{}, false, nil
	}
	return store.orders[id], true, nil
}
func (store *Store) Save(_ context.Context, key string, order domain.Order) error {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.orders[order.ID], store.idempotency[key] = order, order.ID
	return nil
}
func (store *Store) FindByID(_ context.Context, id string) (domain.Order, bool, error) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	order, ok := store.orders[id]
	return order, ok, nil
}
func (store *Store) NewID() string { return fmt.Sprintf("order-%08d", store.sequence.Add(1)) }
