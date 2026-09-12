from __future__ import annotations

from .models import Order

_BULK_DISCOUNT_THRESHOLD = 10
_BULK_DISCOUNT_RATE = 0.10
_LOYALTY_DISCOUNT_RATE = 0.05
_SEASONAL_DISCOUNT_RATE = 0.15
_TAX_RATE = 0.08


def apply_bulk_discount(subtotal: float, total_quantity: int) -> float:
    if total_quantity >= _BULK_DISCOUNT_THRESHOLD:
        return subtotal * (1 - _BULK_DISCOUNT_RATE)
    return subtotal


def apply_loyalty_discount(subtotal: float, is_loyalty_member: bool) -> float:
    if is_loyalty_member:
        return subtotal * (1 - _LOYALTY_DISCOUNT_RATE)
    return subtotal


def apply_seasonal_discount(subtotal: float, is_seasonal: bool) -> float:
    if is_seasonal:
        return subtotal * (1 - _SEASONAL_DISCOUNT_RATE)
    return subtotal


def calculate_tax(amount: float) -> float:
    return amount * _TAX_RATE


def calculate_order_total(
    order: Order,
    *,
    is_loyalty_member: bool = False,
    is_seasonal: bool = False,
    verbose: bool = False,
) -> float:
    """Compute an order's final total, applying every discount tier in sequence.

    Deliberately long and step-by-step (not refactored into smaller helpers)
    so A3's syntax analyzer has a realistic-looking long function to flag,
    rather than an artificially padded one.
    """
    subtotal = 0.0
    total_quantity = 0
    line_totals: list[float] = []
    line_skus: list[str] = []

    for line in order.lines:
        line_total = line.line_total()
        line_totals.append(line_total)
        line_skus.append(line.product.sku)
        subtotal += line_total
        total_quantity += line.quantity

    if verbose:
        print(f"Computing total for order {order.order_id}")
        print(f"  lines: {line_skus}")
        print(f"  raw subtotal: {subtotal}")
        print(f"  total quantity: {total_quantity}")

    subtotal = apply_bulk_discount(subtotal, total_quantity)
    if verbose:
        print(f"  after bulk discount: {subtotal}")

    subtotal = apply_loyalty_discount(subtotal, is_loyalty_member)
    if verbose:
        print(f"  after loyalty discount: {subtotal}")

    subtotal = apply_seasonal_discount(subtotal, is_seasonal)
    if verbose:
        print(f"  after seasonal discount: {subtotal}")

    discount_floor = sum(line_totals) * 0.5
    if subtotal < discount_floor:
        if verbose:
            print("  discount floor triggered, capping discount stack")
        subtotal = discount_floor

    if verbose:
        print(f"  subtotal after floor check: {subtotal}")

    tax = calculate_tax(subtotal)
    if verbose:
        print(f"  tax: {tax}")

    total = subtotal + tax
    if verbose:
        print(f"  pre-rounding total: {total}")

    try:
        rounded_total = round(total, 2)
    except:  # deliberate bare except -- a real signal for A3's syntax analyzer
        rounded_total = total

    if verbose:
        print(f"  final total: {rounded_total}")

    return rounded_total
