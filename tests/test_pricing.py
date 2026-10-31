from decimal import Decimal

import pytest

from emporio.pricing import QuoteError, build_quote, max_installments, price_info


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("120", 2),  # 3x would be R$ 40 < R$ 50
        ("159.90", 3),  # 3x R$ 53,30
        ("349.90", 4),  # 4x R$ 87,48; 5x would be R$ 69,98 < R$ 80
        ("500", 6),
        ("699.90", 6),  # 7x would be R$ 99,99 < R$ 100
        ("700", 7),
        ("1199", 11),
        ("1200", 12),
        ("11499", 12),  # never more than 12x
        ("40", 1),
    ],
)
def test_installment_tiers_follow_policy_3_1(amount, expected):
    assert max_installments(Decimal(amount)) == expected


def test_pix_discount_only_without_promotion(data):
    regular = price_info(data.products[95], None)
    assert (regular.final_price, regular.pix_price) == (Decimal("2199.00"), Decimal("2089.05"))

    promo = price_info(data.products[90], data.active_promotion(90))
    assert promo.final_price == Decimal("1967.18")  # 2399 - 18%
    assert promo.pix_price == promo.final_price  # policy 6.2: no stacking


def test_quote_for_campo_grande(data):
    quote = build_quote(data, [(95, 1)], "Campo Grande")
    assert quote["total"] == "R$ 2.199,00"
    assert quote["shipping"]["fee"] == "grátis"
    assert quote["payment_options"]["pix"].startswith("R$ 2.089,05")
    assert quote["payment_options"]["credit_card"] == "até 12x sem juros de R$ 183,25"
    assert quote["payment_options"]["combined_payment_allowed"] is True


def test_quote_charges_local_fee_below_500(data):
    quote = build_quote(data, [(119, 1)], "campo grande")
    assert quote["shipping"]["fee"] == "R$ 35,00"
    assert quote["total"] == "R$ 194,90"
    assert quote["payment_options"]["combined_payment_allowed"] is False


def test_quote_outside_campo_grande_is_calculated_at_checkout(data):
    quote = build_quote(data, [(139, 1)], "Dourados")
    assert "CEP" in quote["shipping"]["fee"]
    assert any("frete especial" in note for note in quote["shipping"]["notes"])


@pytest.mark.parametrize(
    ("items", "message"),
    [
        ([(96, 1)], "esgotado"),
        ([(130, 1)], "lançamento em breve"),
        ([(129, 2)], "Só há 1 unidade"),
        ([(999, 1)], "não existe"),
        ([], "ao menos um produto"),
    ],
)
def test_quote_rejects_what_cannot_be_sold(data, items, message):
    with pytest.raises(QuoteError, match=message):
        build_quote(data, items, None)
