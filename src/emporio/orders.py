"""Order status lookup with identity verification.

Order data is personal data (policy 9, LGPD). The check happens here, in code,
and the model never sees the record unless it passes - so no prompt can talk
the agent into revealing someone else's order. Failures return one generic
message, whether the order does not exist or the contact does not match, so
order numbers cannot be enumerated.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from emporio.data.models import Customer, Order, OrderStatus, StoreData
from emporio.money import format_brl

REGRET_DAYS = 7  # §4.1, calendar days after receipt (online purchases)
PREFERENCE_EXCHANGE_DAYS = 7  # §4.3 (counted from receipt - README, Assumptions)
DEFECT_EXCHANGE_DAYS = 30  # §4.2, calendar days after the purchase
LEGAL_WARRANTY_DAYS = 90  # §8.1, from receipt
MAX_FAILED_ATTEMPTS = 3

STATUS = {
    OrderStatus.PENDING: (
        "aguardando pagamento",
        "Pedido registrado, aguardando a confirmação do pagamento.",
    ),
    OrderStatus.CONFIRMED: (
        "pagamento confirmado",
        "Pagamento confirmado; o pedido está em separação e ainda não foi despachado.",
    ),
    OrderStatus.SHIPPED: ("enviado", "Pedido despachado e a caminho."),
    OrderStatus.DELIVERED: ("entregue", "Pedido entregue."),
    OrderStatus.CANCELLED: ("cancelado", "Pedido cancelado."),
}
PAYMENT = {"pix": "PIX", "debit": "cartão de débito", "boleto": "boleto bancário"}

NOT_FOUND = (
    "Não localizei um pedido com esse número e esse contato. Peça para o cliente "
    "conferir o número do pedido e o e-mail ou telefone usados na compra."
)
LOCKED = (
    "Limite de tentativas de verificação atingido nesta conversa. Não tente de novo: "
    "ofereça encaminhar o caso para a equipe (create_support_ticket)."
)


def _digits(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[2:] if digits.startswith("55") and len(digits) > 11 else digits


def contact_matches(customer: Customer, contact: str) -> bool:
    """E-mail (case-insensitive) or phone (last 8 digits; area code checked when given)."""
    contact = contact.strip().lower()
    if "@" in contact:
        return contact == customer.email
    given, known = _digits(contact), _digits(customer.phone)
    if len(given) < 8 or given[-8:] != known[-8:]:
        return False
    return len(given) < 10 or given[:2] == known[:2]


def payment_label(method: str) -> str:
    if method.startswith("credit_"):
        return f"cartão de crédito em {method.removeprefix('credit_')}"
    return PAYMENT.get(method, method)


def _deadline(start: date, days: int, today: date, rule: str) -> str:
    end = start + timedelta(days=days)
    state = "dentro do prazo" if today <= end else "prazo encerrado"
    return f"{end:%d/%m/%Y} ({rule}) - {state}"


class OrderDesk:
    def __init__(self, data: StoreData) -> None:
        self.data = data

    def owner_of(self, order_id: int) -> int | None:
        order = self.data.orders.get(order_id)
        return order.customer_id if order else None

    def lookup(
        self,
        order_id: int,
        contact: str | None,
        verified_customer_id: int | None,
        failed_attempts: int,
        today: date,
    ) -> tuple[dict, bool]:
        """Returns (tool result, verification_failed)."""
        if failed_attempts >= MAX_FAILED_ATTEMPTS:
            return {"found": False, "message": LOCKED}, False
        order = self.data.orders.get(order_id)
        customer = self.data.customers.get(order.customer_id) if order else None
        verified = bool(customer) and (
            customer.id == verified_customer_id or (contact and contact_matches(customer, contact))
        )
        if not verified:
            if not contact and verified_customer_id is None:
                return {
                    "found": False,
                    "message": "Peça o e-mail ou telefone usado na compra antes de consultar o pedido.",
                }, False
            return {"found": False, "message": NOT_FOUND}, True
        return {"found": True, "order": self._view(order, customer, today)}, False

    def orders_of(self, customer_id: int) -> list[dict]:
        """Short list for a customer already identified by the channel."""
        return [
            {
                "order_id": o.id,
                "date": f"{o.order_date:%d/%m/%Y}",
                "status": STATUS[o.status][0],
                "total": format_brl(o.total),
            }
            for o in sorted(self.data.orders.values(), key=lambda o: o.order_date, reverse=True)
            if o.customer_id == customer_id
        ]

    def _view(self, order: Order, customer: Customer, today: date) -> dict:
        label, detail = STATUS[order.status]
        view: dict = {
            "order_id": order.id,
            "customer_first_name": customer.first_name,
            "status": label,
            "status_detail": detail,
            "order_date": f"{order.order_date:%d/%m/%Y}",
            "items": [
                {"product": self.data.products[i.product_id].name, "quantity": i.quantity}
                for i in order.items
            ],
            "total": format_brl(order.total),
            "payment_method": payment_label(order.payment_method),
        }
        if order.tracking_code:
            view["tracking_code"] = order.tracking_code
            view["tracking_where"] = "site dos Correios ou da Jadlog"
        if order.estimated_delivery:
            view["estimated_delivery"] = f"{order.estimated_delivery:%d/%m/%Y}"
            late_by = (today - order.estimated_delivery).days
            if order.status is OrderStatus.SHIPPED and late_by > 0:
                view["delay"] = f"previsão de entrega ultrapassada há {late_by} dia(s)"
        if order.notes:
            view["notes"] = order.notes
        if order.status in (OrderStatus.PENDING, OrderStatus.CONFIRMED):
            view["before_shipping"] = (
                "Ainda não despachado. Cancelamentos ou alterações não "
                "estão definidos no manual: registre a solicitação para a equipe."
            )
        if order.status is OrderStatus.DELIVERED and order.estimated_delivery:
            received = order.estimated_delivery
            view["after_sales"] = {
                "receipt_date_used": f"{received:%d/%m/%Y} (previsão de entrega; a data real de "
                "recebimento não consta no sistema)",
                "days_since_receipt": (today - received).days,
                "regret_return_until": _deadline(
                    received, REGRET_DAYS, today, "arrependimento, 7 dias após o recebimento, política 4.1"
                ),
                "preference_exchange_until": _deadline(
                    received, PREFERENCE_EXCHANGE_DAYS, today, "troca por preferência, 7 dias, política 4.3"
                ),
                "defect_exchange_until": _deadline(
                    order.order_date,
                    DEFECT_EXCHANGE_DAYS,
                    today,
                    "troca por defeito, 30 dias após a compra, política 4.2",
                ),
                "legal_warranty_until": _deadline(
                    received,
                    LEGAL_WARRANTY_DAYS,
                    today,
                    "garantia legal, 90 dias após o recebimento, política 8.1",
                ),
            }
        return view
