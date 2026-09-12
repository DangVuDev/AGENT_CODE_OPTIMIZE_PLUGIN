from inventory.models import Order, Product
from inventory.pricing import calculate_order_total


def test_calculate_order_total_benchmark(benchmark) -> None:  # noqa: ANN001
    product = Product(sku="A1", name="Widget", unit_price=10.0)
    order = Order(order_id="O-bench")
    order.add_line(product, 3)

    benchmark(calculate_order_total, order)
