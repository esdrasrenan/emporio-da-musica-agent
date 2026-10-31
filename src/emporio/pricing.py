"""Price, payment and shipping rules from the store policy manual.

Numbers are computed here rather than by the LLM: models are unreliable at
arithmetic with thresholds, and policy 7.1 forbids quoting prices or deadlines
"without consulting the system". The policy text still explains the conditions
to the customer; each constant below cites the section it comes from.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from emporio.data.models import Product, Promotion, StoreData
from emporio.money import format_brl, to_money
from emporio.text import fold

PIX_DISCOUNT = Decimal("0.05")  # §3: 5% off the list price, paid in full
# §3.1: (first, last installment count, minimum installment value). The manual's
# "sem valor mínimo de parcela (exceto abaixo de R$ 50,00)" is read as "each
# installment must be at least R$ 50" - see README, Assumptions.
INSTALLMENT_TIERS = ((2, 3, Decimal("50")), (4, 6, Decimal("80")), (7, 12, Decimal("100")))
COMBINED_PAYMENT_ABOVE = Decimal("2000")  # §3.1: PIX + card only above R$ 2.000,00
FREE_SHIPPING_FROM = Decimal("500")  # §5.1: free above R$ 500 (exactly R$ 500 assumed free)
LOCAL_SHIPPING_FEE = Decimal("35")  # §5.1
LOCAL_CITIES = ("campo grande",)  # §5.1 metro area; only the capital is confirmed in the data


@dataclass(frozen=True)
class PriceInfo:
    list_price: Decimal
    promotion: Promotion | None
    final_price: Decimal
    pix_price: Decimal
    max_installments: int

    @property
    def installment_value(self) -> Decimal:
        return to_money(self.final_price / self.max_installments)


def max_installments(amount: Decimal) -> int:
    """Largest interest-free installment count allowed for ``amount`` (§3.1)."""
    for first, last, minimum in reversed(INSTALLMENT_TIERS):
        for count in range(last, first - 1, -1):
            if amount / count >= minimum:
                return count
    return 1


def price_info(product: Product, promotion: Promotion | None) -> PriceInfo:
    if promotion:
        final = to_money(product.price * (100 - promotion.discount_percent) / 100)
        pix = final  # §6.2: the PIX discount does not apply on top of a promotion
    else:
        final = product.price
        pix = to_money(product.price * (1 - PIX_DISCOUNT))
    return PriceInfo(product.price, promotion, final, pix, max_installments(final))


def describe_installments(amount: Decimal) -> str:
    count = max_installments(amount)
    if count == 1:
        return f"à vista no cartão ({format_brl(amount)})"
    return f"até {count}x sem juros de {format_brl(to_money(amount / count))}"


@dataclass(frozen=True)
class QuoteLine:
    product: Product
    quantity: int
    price: PriceInfo

    @property
    def total(self) -> Decimal:
        return self.price.final_price * self.quantity

    @property
    def pix_total(self) -> Decimal:
        return self.price.pix_price * self.quantity


class QuoteError(ValueError):
    """Raised for items that cannot be quoted; the message is shown to the model."""


def build_quote(data: StoreData, items: list[tuple[int, int]], city: str | None) -> dict:
    """Total, payment options and delivery for a cart, as a JSON-ready dict."""
    if not items:
        raise QuoteError("Informe ao menos um produto (product_id) e quantidade.")
    lines: list[QuoteLine] = []
    for product_id, quantity in items:
        product = data.products.get(product_id)
        if product is None:
            raise QuoteError(f"Produto {product_id} não existe. Use search_products para achar o id.")
        if quantity < 1:
            raise QuoteError("A quantidade deve ser pelo menos 1.")
        if not product.purchasable:
            raise QuoteError(f"{product.name} não pode ser vendido agora: {product.availability}.")
        if quantity > product.stock:
            raise QuoteError(f"Só há {product.stock} unidade(s) de {product.name} em estoque.")
        lines.append(QuoteLine(product, quantity, price_info(product, data.active_promotion(product_id))))

    subtotal = sum((line.total for line in lines), Decimal(0))
    pix_subtotal = sum((line.pix_total for line in lines), Decimal(0))
    shipping = _shipping(city, subtotal, lines)
    fee = shipping.get("fee_value", Decimal(0))
    total = subtotal + fee
    return {
        "items": [
            {
                "product_id": line.product.id,
                "name": line.product.name,
                "quantity": line.quantity,
                "unit_price": format_brl(line.price.final_price),
                "promotion": _promo_label(line.price),
                "line_total": format_brl(line.total),
            }
            for line in lines
        ],
        "subtotal": format_brl(subtotal),
        "shipping": {k: v for k, v in shipping.items() if k != "fee_value"},
        "total": format_brl(total),
        "payment_options": {
            "pix": f"{format_brl(pix_subtotal + fee)} à vista "
            + (
                "(5% de desconto nos itens sem promoção)"
                if pix_subtotal < subtotal
                else "(itens em promoção não recebem o desconto PIX)"
            ),
            "credit_card": describe_installments(total),
            "debit_card": f"{format_brl(total)} à vista",
            "boleto": f"{format_brl(total)} à vista (compensação em até 3 dias úteis)",
            "combined_payment_allowed": total > COMBINED_PAYMENT_ABOVE,
        },
    }


def _promo_label(price: PriceInfo) -> str | None:
    if not price.promotion:
        return None
    return (
        f"{price.promotion.description}: {price.promotion.discount_percent.normalize()}% "
        f"de desconto sobre {format_brl(price.list_price)}"
    )


def _shipping(city: str | None, subtotal: Decimal, lines: list[QuoteLine]) -> dict:
    notes: list[str] = []
    bulky = [line.product.name for line in lines if "bateria acustica" in fold(line.product.name)]
    if bulky:
        notes.append(
            f"Instrumento de grande porte ({', '.join(bulky)}): pode exigir frete "
            "especial com cotação individual (política 5.2)."
        )
    if city and fold(city).strip() in LOCAL_CITIES:
        fee = Decimal(0) if subtotal >= FREE_SHIPPING_FROM else LOCAL_SHIPPING_FEE
        shipping = {
            "destination": city,
            "fee": "grátis" if fee == 0 else format_brl(fee),
            "fee_value": fee,
            "delivery_time": "1 a 3 dias úteis, por motoboy próprio (o cliente recebe uma ligação antes)",
        }
    else:
        shipping = {
            "destination": city or "não informado",
            "fee": "calculado no fechamento do pedido pelo CEP, peso e dimensões",
            "delivery_time": "PAC 5 a 12, SEDEX 2 a 5 ou Jadlog 3 a 8 dias úteis (com seguro e rastreio)",
        }
        if not city:
            notes.append(
                "Cidade não informada: em Campo Grande o frete é grátis acima de "
                "R$ 500,00 (abaixo disso, R$ 35,00)."
            )
    if notes:
        shipping["notes"] = notes
    return shipping
