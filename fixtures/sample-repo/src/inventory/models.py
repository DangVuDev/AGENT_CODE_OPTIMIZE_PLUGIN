from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Product:
    sku: str
    name: str
    unit_price: float
    quantity_on_hand: int = 0

    def total_value(self) -> float:
        return self.unit_price * self.quantity_on_hand


@dataclass
class OrderLine:
    product: Product
    quantity: int

    def line_total(self) -> float:
        return self.product.unit_price * self.quantity


@dataclass
class Order:
    order_id: str
    lines: list[OrderLine] = field(default_factory=list)

    def add_line(self, product: Product, quantity: int) -> None:
        self.lines.append(OrderLine(product=product, quantity=quantity))

    def subtotal(self) -> float:
        return sum(line.line_total() for line in self.lines)

    def total_quantity(self) -> int:
        return sum(line.quantity for line in self.lines)
