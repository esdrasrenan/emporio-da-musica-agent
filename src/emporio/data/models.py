"""Typed domain records built from the store's CSV exports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class ProductStatus(StrEnum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class Availability(StrEnum):
    """What the customer can do with a product today."""

    AVAILABLE = "disponível"
    OUT_OF_STOCK = "esgotado"
    DISCONTINUED = "descontinuado (fora de linha)"
    COMING_SOON = "lançamento em breve (ainda não disponível para venda)"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


LOW_STOCK_THRESHOLD = 2


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    description: str
    slug: str


@dataclass(frozen=True)
class Promotion:
    id: int
    product_id: int
    discount_percent: Decimal
    description: str
    is_active: bool


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    category_id: int
    price: Decimal
    stock: int
    status: ProductStatus
    specs: dict[str, str]
    created_at: date
    brand: str | None
    # None when the original text contradicts the product name (see data quality).
    description: str | None
    raw_description: str

    @property
    def availability(self) -> Availability:
        if self.status is ProductStatus.DISCONTINUED:
            return Availability.DISCONTINUED
        if self.status is ProductStatus.COMING_SOON:
            return Availability.COMING_SOON
        return Availability.AVAILABLE if self.stock > 0 else Availability.OUT_OF_STOCK

    @property
    def purchasable(self) -> bool:
        return self.availability is Availability.AVAILABLE

    @property
    def low_stock(self) -> bool:
        return self.purchasable and self.stock <= LOW_STOCK_THRESHOLD


@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    phone: str
    email: str
    city: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0]


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int


@dataclass(frozen=True)
class Order:
    id: int
    customer_id: int
    order_date: date
    status: OrderStatus
    total: Decimal
    payment_method: str
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: tuple[OrderItem, ...] = ()


@dataclass(frozen=True)
class DataFinding:
    """A data-quality issue found while loading, and how the agent handles it."""

    table: str
    record: str
    issue: str
    handling: str


@dataclass
class StoreData:
    categories: dict[int, Category]
    products: dict[int, Product]
    promotions: list[Promotion]
    customers: dict[int, Customer]
    orders: dict[int, Order]
    findings: list[DataFinding] = field(default_factory=list)

    def category_by_slug(self, slug: str) -> Category | None:
        return next((c for c in self.categories.values() if c.slug == slug), None)

    def active_promotion(self, product_id: int) -> Promotion | None:
        """Best active promotion. Promotions never stack (policy 6.2), so the
        customer gets the single highest discount when several are active."""
        active = [p for p in self.promotions if p.product_id == product_id and p.is_active]
        return max(active, key=lambda p: p.discount_percent, default=None)
