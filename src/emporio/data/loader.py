"""Load the store's CSV exports into typed records and record data-quality findings.

The raw files are never modified. Every cleaning rule lives here and is reported
as a ``DataFinding`` so the business can fix the source instead of relying on
the agent's workarounds (see docs/data-quality.md).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from emporio.data.models import (
    Category,
    Customer,
    DataFinding,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductStatus,
    Promotion,
    StoreData,
)
from emporio.money import to_money
from emporio.text import contains_phrase, slugify, tokens

FILE_PREFIX = "desafio_tecnico_ai_eng - "

# Brands sold by the store. Model names that imply a brand are mapped too, so a
# description such as "Contrabaixo Precision" is recognised as Fender.
BRANDS = (
    "crafter",
    "fender",
    "giannini",
    "gibson",
    "ibanez",
    "kala",
    "kalani",
    "korg",
    "martin",
    "music man",
    "nord",
    "ohana",
    "pearl",
    "prs",
    "roland",
    "rozini",
    "shelby",
    "tagima",
    "takamine",
    "tama",
    "taylor",
    "yamaha",
)
MODEL_BRANDS = {
    "precision": "fender",
    "jazz bass": "fender",
    "stratocaster": "fender",
    "telecaster": "fender",
    "les paul": "gibson",
    "stingray": "music man",
}

INSTALLMENT_MINIMUMS = (
    (range(2, 4), Decimal("50")),
    (range(4, 7), Decimal("80")),
    (range(7, 13), Decimal("100")),
)


def _read(data_dir: Path, table: str) -> list[dict[str, str]]:
    path = data_dir / f"{FILE_PREFIX}{table}.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def find_brands(text: str) -> set[str]:
    words = tokens(text)
    found = {brand for brand in BRANDS if contains_phrase(words, brand)}
    found |= {brand for model, brand in MODEL_BRANDS.items() if contains_phrase(words, model)}
    return found


def _product_brand(name: str) -> str | None:
    words = tokens(name)
    joined = f" {' '.join(words)} "
    positions = {b: joined.find(f" {b} ") for b in BRANDS if f" {b} " in joined}
    return min(positions, key=positions.get) if positions else None


def load_store_data(data_dir: Path) -> StoreData:
    findings: list[DataFinding] = []

    categories = {
        int(r["category_id"]): Category(
            id=int(r["category_id"]),
            name=r["name"],
            description=r["description"],
            slug=slugify(r["name"]),
        )
        for r in _read(data_dir, "categories")
    }

    products: dict[int, Product] = {}
    for r in _read(data_dir, "products"):
        pid = int(r["product_id"])
        brand = _product_brand(r["name"])
        description: str | None = r["description"]
        conflicting = find_brands(r["description"]) - ({brand} if brand else set())
        if conflicting:
            description = None
            findings.append(
                DataFinding(
                    "products",
                    f"{pid} - {r['name']}",
                    f"description mentions {', '.join(sorted(conflicting))} but the product is "
                    f"{brand or 'unbranded'}",
                    "description hidden from the agent; name, specs and price are still used",
                )
            )
        product = Product(
            id=pid,
            name=r["name"],
            category_id=int(r["category_id"]),
            price=to_money(r["price_brl"]),
            stock=int(r["stock_quantity"]),
            status=ProductStatus(r["status"]),
            specs={k: str(v) for k, v in json.loads(r["specs"]).items()},
            created_at=date.fromisoformat(r["created_at"]),
            brand=brand,
            description=description,
            raw_description=r["description"],
        )
        if product.status is not ProductStatus.ACTIVE and product.stock > 0:
            findings.append(
                DataFinding(
                    "products",
                    f"{pid} - {product.name}",
                    f"status '{product.status}' but stock_quantity={product.stock}",
                    "treated as not purchasable; status wins over stock",
                )
            )
        products[pid] = product

    for category in categories.values():
        if not any(p.category_id == category.id for p in products.values()):
            findings.append(
                DataFinding(
                    "categories",
                    f"{category.id} - {category.name}",
                    "category has no products",
                    "searches in it return an explicit 'no items in this category' note",
                )
            )

    promotions = [
        Promotion(
            id=int(r["promotion_id"]),
            product_id=int(r["product_id"]),
            discount_percent=Decimal(r["discount_percent"]),
            description=r["description"],
            is_active=r["is_active"] == "1",
        )
        for r in _read(data_dir, "promotions")
    ]
    per_product: dict[int, list[Promotion]] = defaultdict(list)
    for promo in promotions:
        per_product[promo.product_id].append(promo)
    for pid, promos in sorted(per_product.items()):
        if len(promos) > 1:
            findings.append(
                DataFinding(
                    "promotions",
                    f"product {pid}",
                    f"{len(promos)} promotions registered ({sum(p.is_active for p in promos)} active)",
                    "only active promotions count; if several are active the best one applies",
                )
            )

    customers = {
        int(r["customer_id"]): Customer(
            id=int(r["customer_id"]),
            name=r["name"],
            phone=r["phone"],
            email=r["email"].lower(),
            city=r["city"],
        )
        for r in _read(data_dir, "customers")
    }
    by_email: dict[str, list[int]] = defaultdict(list)
    for customer in customers.values():
        by_email[customer.email].append(customer.id)
    for email, ids in by_email.items():
        if len(ids) > 1:
            findings.append(
                DataFinding(
                    "customers",
                    f"ids {', '.join(map(str, ids))}",
                    f"e-mail {email} is shared",
                    "identity checks compare the contact with the order's own customer, "
                    "never resolve a customer from the e-mail alone",
                )
            )

    items: dict[int, list[OrderItem]] = defaultdict(list)
    for r in _read(data_dir, "order_items"):
        items[int(r["order_id"])].append(
            OrderItem(product_id=int(r["product_id"]), quantity=int(r["quantity"]))
        )

    orders: dict[int, Order] = {}
    for r in _read(data_dir, "orders"):
        oid = int(r["order_id"])
        order = Order(
            id=oid,
            customer_id=int(r["customer_id"]),
            order_date=date.fromisoformat(r["order_date"]),
            status=OrderStatus(r["status"]),
            total=to_money(r["total_brl"]),
            payment_method=r["payment_method"],
            tracking_code=r["tracking_code"] or None,
            estimated_delivery=date.fromisoformat(r["estimated_delivery"])
            if r["estimated_delivery"]
            else None,
            notes=r["notes"] or None,
            items=tuple(items.get(oid, ())),
        )
        orders[oid] = order
        _check_order(order, products, findings)

    return StoreData(categories, products, promotions, customers, orders, findings)


def _check_order(order: Order, products: dict[int, Product], findings: list[DataFinding]) -> None:
    label = f"order {order.id}"
    listed = sum((products[i.product_id].price * i.quantity for i in order.items), Decimal(0))
    if listed != order.total:
        findings.append(
            DataFinding(
                "orders",
                label,
                f"total_brl {order.total} differs from current list prices ({listed})",
                "the recorded total is reported as is (it is what the customer was charged)",
            )
        )
    if order.payment_method.startswith("credit_"):
        installments = int(order.payment_method.removeprefix("credit_").rstrip("x"))
        for allowed, minimum in INSTALLMENT_MINIMUMS:
            if installments in allowed and order.total / installments < minimum:
                findings.append(
                    DataFinding(
                        "orders",
                        label,
                        f"{installments}x installments of {to_money(order.total / installments)} "
                        f"are below the R$ {minimum} minimum of policy 3.1",
                        "informational: new quotes always follow policy 3.1",
                    )
                )
    for item in order.items:
        if products[item.product_id].status is ProductStatus.COMING_SOON:
            findings.append(
                DataFinding(
                    "orders",
                    label,
                    f"contains product {item.product_id}, which is still 'coming_soon'",
                    "informational: the order is reported normally",
                )
            )
