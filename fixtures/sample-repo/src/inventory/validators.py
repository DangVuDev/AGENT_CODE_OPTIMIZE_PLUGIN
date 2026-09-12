from __future__ import annotations

import os  # unused import -- a real, deliberate lint violation for A2.60/A3.30
from typing import Any

from .models import Order, Product


def validate_product(product: Product) -> list[str]:
    errors: list[str] = []
    if not product.sku:
        errors.append("sku must not be empty")
    if product.unit_price < 0:
        errors.append("unit_price must not be negative")
    if product.quantity_on_hand < 0:
        errors.append("quantity_on_hand must not be negative")
    return errors


def validate_order(order: Order) -> list[str]:
    errors: list[str] = []
    if not order.order_id:
        errors.append("order_id must not be empty")
    if not order.lines:
        errors.append("order must contain at least one line")
    for line in order.lines:
        if line.quantity <= 0:
            errors.append(f"line quantity for {line.product.sku} must be positive")
    return errors


def coerce_quantity(value: Any) -> int:
    return int(value)
