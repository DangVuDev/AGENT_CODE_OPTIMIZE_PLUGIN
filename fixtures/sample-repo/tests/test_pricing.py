from inventory.models import Order, Product
from inventory.pricing import apply_bulk_discount, apply_loyalty_discount, calculate_order_total


def test_apply_bulk_discount_below_threshold() -> None:
    assert apply_bulk_discount(100.0, 5) == 100.0


def test_apply_bulk_discount_at_threshold() -> None:
    assert apply_bulk_discount(100.0, 10) == 90.0


def test_apply_loyalty_discount() -> None:
    assert apply_loyalty_discount(100.0, True) == 95.0


def test_calculate_order_total_simple() -> None:
    product = Product(sku="A1", name="Widget", unit_price=10.0)
    order = Order(order_id="O1")
    order.add_line(product, 1)
    total = calculate_order_total(order)
    # BUG (deliberate): the real tax rate is 8%, so a $10 order totals
    # $10.80, not $11.00. This assertion is intentionally wrong so Lane 1
    # has one real, reproducible failing test to collect and analyze
    # end-to-end, instead of a synthetic always-failing stand-in.
    assert total == 11.0


def test_calculate_order_total_with_bulk_discount() -> None:
    product = Product(sku="A1", name="Widget", unit_price=10.0)
    order = Order(order_id="O2")
    order.add_line(product, 10)
    total = calculate_order_total(order)
    # 10 units * $10 = $100 -> 10% bulk discount -> $90 -> +8% tax -> $97.20
    assert total == 97.2
