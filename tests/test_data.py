from decimal import Decimal

from emporio.data.models import Availability
from emporio.money import format_brl
from emporio.text import fold, singular, tokens


def test_loads_every_table(data):
    assert (
        len(data.categories),
        len(data.products),
        len(data.promotions),
        len(data.customers),
        len(data.orders),
    ) == (9, 65, 25, 50, 20)
    assert sum(len(o.items) for o in data.orders.values()) == 22


def test_descriptions_contradicting_the_product_brand_are_hidden(data):
    hidden = {pid for pid, p in data.products.items() if p.description is None}
    assert hidden == {135, 136, 137, 139, 140, 142, 144}
    assert "Fender" in data.products[135].raw_description  # raw text is kept for audit
    assert data.products[95].description  # consistent descriptions are untouched


def test_availability_follows_status_before_stock(data):
    assert data.products[95].availability is Availability.AVAILABLE
    assert data.products[96].availability is Availability.OUT_OF_STOCK
    assert data.products[113].availability is Availability.DISCONTINUED
    assert data.products[136].availability is Availability.DISCONTINUED  # stock 13, still not sold
    assert data.products[130].availability is Availability.COMING_SOON  # stock 2, still not sold
    assert data.products[129].low_stock


def test_only_active_promotions_count(data):
    assert data.active_promotion(95) is None  # "Oferta Especial" 12% is inactive
    promo = data.active_promotion(90)
    assert promo and promo.discount_percent == Decimal("18")


def test_findings_cover_known_issues(data):
    issues = " | ".join(f"{f.record} {f.issue}" for f in data.findings)
    for expected in (
        "order 3",
        "order 20",
        "bruno.martins@jmail.com",
        "Instrumentos de Sopro (Madeiras)",
        "130 - Kala",
    ):
        assert expected in issues


def test_text_helpers():
    assert fold("Violão Elétrico") == "violao eletrico"
    assert tokens("GD-20, Aço!") == ["gd", "20", "aco"]
    assert [singular(t) for t in ("violoes", "cordas", "metais", "ukuleles", "bass")] == [
        "violao",
        "corda",
        "metal",
        "ukulele",
        "bass",
    ]
    assert format_brl(Decimal("2199")) == "R$ 2.199,00"
    assert format_brl(Decimal("1967.175")) == "R$ 1.967,18"
