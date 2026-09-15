package checkout

import (
	"context"
	"errors"
	"fmt"
	"time"

	domain "example.com/go-checkout/internal/domain/checkout"
)

var (
	ErrProductNotFound   = errors.New("product not found")
	ErrInsufficientStock = errors.New("insufficient stock")
	ErrOrderNotFound     = errors.New("order not found")
)

type Product struct {
	SKU, Name             string
	PriceCents, Available int64
}
type ItemInput struct {
	SKU      string `json:"sku"`
	Quantity int64  `json:"quantity"`
}
type Command struct {
	CustomerID     string      `json:"customer_id"`
	Currency       string      `json:"currency"`
	CouponCode     string      `json:"coupon_code"`
	Items          []ItemInput `json:"items"`
	IdempotencyKey string      `json:"-"`
}
type Catalog interface {
	FindProduct(context.Context, string) (Product, error)
}
type CouponBook interface {
	DiscountBasisPoints(context.Context, string) (int64, error)
}
type OrderRepository interface {
	FindByIdempotencyKey(context.Context, string) (domain.Order, bool, error)
	Save(context.Context, string, domain.Order) error
	FindByID(context.Context, string) (domain.Order, bool, error)
}
type IDGenerator interface{ NewID() string }

type Service struct {
	catalog Catalog
	coupons CouponBook
	orders  OrderRepository
	ids     IDGenerator
	now     func() time.Time
}

func NewService(catalog Catalog, coupons CouponBook, orders OrderRepository, ids IDGenerator) *Service {
	return &Service{catalog: catalog, coupons: coupons, orders: orders, ids: ids, now: time.Now}
}

func (service *Service) Checkout(ctx context.Context, command Command) (domain.Order, bool, error) {
	if command.CustomerID == "" || command.IdempotencyKey == "" {
		return domain.Order{}, false, errors.New("customer_id and Idempotency-Key are required")
	}
	if existing, found, err := service.orders.FindByIdempotencyKey(ctx, command.IdempotencyKey); err != nil {
		return domain.Order{}, false, err
	} else if found {
		return existing, true, nil
	}
	lines := make([]domain.Line, 0, len(command.Items))
	for _, input := range command.Items {
		product, err := service.catalog.FindProduct(ctx, input.SKU)
		if err != nil {
			return domain.Order{}, false, fmt.Errorf("%w: %s", ErrProductNotFound, input.SKU)
		}
		if input.Quantity > product.Available {
			return domain.Order{}, false, fmt.Errorf("%w: %s", ErrInsufficientStock, input.SKU)
		}
		lines = append(lines, domain.Line{SKU: product.SKU, Name: product.Name, Quantity: input.Quantity, UnitPriceCents: product.PriceCents})
	}
	discount, err := service.coupons.DiscountBasisPoints(ctx, command.CouponCode)
	if err != nil {
		return domain.Order{}, false, err
	}
	order, err := domain.Price(lines, command.Currency, discount)
	if err != nil {
		return domain.Order{}, false, err
	}
	order.ID, order.CustomerID, order.CreatedAt = service.ids.NewID(), command.CustomerID, service.now().UTC()
	if err := service.orders.Save(ctx, command.IdempotencyKey, order); err != nil {
		return domain.Order{}, false, err
	}
	return order, false, nil
}

func (service *Service) GetOrder(ctx context.Context, id string) (domain.Order, error) {
	order, found, err := service.orders.FindByID(ctx, id)
	if err != nil {
		return domain.Order{}, err
	}
	if !found {
		return domain.Order{}, ErrOrderNotFound
	}
	return order, nil
}
