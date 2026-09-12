from inventory.models import Order, Product
from inventory.validators import coerce_quantity, validate_order, validate_product


def test_validate_product_ok() -> None:
    product = Product(sku="A1", name="Widget", unit_price=2.5)
    assert validate_product(product) == []


def test_validate_product_negative_price() -> None:
    product = Product(sku="A1", name="Widget", unit_price=-1.0)
    assert validate_product(product) != []


def test_validate_order_empty() -> None:
    order = Order(order_id="O1")
    assert validate_order(order) != []


def test_coerce_quantity() -> None:
    assert coerce_quantity("3") == 3
