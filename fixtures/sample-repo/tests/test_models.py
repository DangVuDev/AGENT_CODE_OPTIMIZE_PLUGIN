from inventory.models import Order, Product


def test_product_total_value() -> None:
    product = Product(sku="A1", name="Widget", unit_price=2.5, quantity_on_hand=4)
    assert product.total_value() == 10.0


def test_order_subtotal() -> None:
    product = Product(sku="A1", name="Widget", unit_price=2.5)
    order = Order(order_id="O1")
    order.add_line(product, 3)
    assert order.subtotal() == 7.5


def test_order_total_quantity() -> None:
    product = Product(sku="A1", name="Widget", unit_price=2.5)
    order = Order(order_id="O1")
    order.add_line(product, 3)
    order.add_line(product, 2)
    assert order.total_quantity() == 5
