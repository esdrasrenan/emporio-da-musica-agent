import pytest


def ids(result):
    return [p.id for p in result.products]


def test_browse_violoes_up_to_1000(catalog):
    result = catalog.search(category="violoes", max_price=1000, limit=20)
    assert sorted(ids(result)) == [81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 107, 112]
    assert ids(result)[0] == 83  # cheapest first
    assert all(p.purchasable for p in result.products)  # 96 (sold out) and 113 (discontinued) left out


@pytest.mark.parametrize("query", ["Takamine GD20", "takamime gd 20", "GD-20", "violão takamine gd20"])
def test_named_product_is_found_despite_typos_and_spacing(catalog, query):
    assert ids(catalog.search(query=query))[0] == 95


def test_named_unavailable_product_is_returned_with_alternatives(catalog):
    result = catalog.search(query="Giannini GF-3D")
    assert ids(result)[0] == 96
    view = catalog.view(result.products[0], with_alternatives=True)
    assert view["availability"] == "esgotado"
    alternatives = view["available_alternatives"]
    assert len(alternatives) == 3
    assert all(catalog.data.products[a["product_id"]].specs["strings"] == "steel" for a in alternatives)


def test_empty_category_is_explained(catalog):
    result = catalog.search(query="saxofone")
    assert result.total == 0
    assert any("Sopro (Madeiras)" in note for note in result.notes)


def test_unknown_terms_are_reported(catalog):
    result = catalog.search(query="guitarra canhoto")
    assert result.total == 5
    assert any("canhoto" in note for note in result.notes)


def test_on_sale_only(catalog):
    assert sorted(ids(catalog.search(on_sale_only=True))) == [90, 94, 121, 127]


def test_price_filter_uses_promotional_price(catalog):
    # Ohana CK-20 lists at R$ 549,00 but costs R$ 439,20 with the active 20% promotion.
    assert 121 in ids(catalog.search(category="ukuleles", max_price=450, limit=20))


def test_view_shows_promotion_transparently(catalog, data):
    view = catalog.view(data.products[90])
    assert view["list_price"] == "R$ 2.399,00"
    assert view["promotion"]["discount"] == "18%"
    assert view["promotion"]["promotional_price"] == "R$ 1.967,18"
    assert "pix_price" not in view


def test_view_hides_price_of_items_not_for_sale(catalog, data):
    for pid in (113, 130):
        view = catalog.view(data.products[pid])
        assert "list_price" not in view and "credit_card" not in view


def test_view_hides_contradictory_description(catalog, data):
    assert "description" not in catalog.view(data.products[135])
