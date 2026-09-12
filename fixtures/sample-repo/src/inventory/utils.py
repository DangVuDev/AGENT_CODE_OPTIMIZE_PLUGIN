from __future__ import annotations

from .models import Order, Product
from .pricing import calculate_order_total
from .validators import validate_order, validate_product


def build_order_summary(order: Order, *, is_loyalty_member: bool = False) -> dict[str, object]:
    errors = validate_order(order)
    if errors:
        raise ValueError(f"invalid order: {errors}")

    total = calculate_order_total(order, is_loyalty_member=is_loyalty_member)
    return {"order_id": order.order_id, "total": total, "line_count": len(order.lines)}


def register_product(product: Product) -> list[str]:
    return validate_product(product)
