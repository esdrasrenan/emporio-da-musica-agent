from datetime import date

import pytest

from emporio.orders import LOCKED, NOT_FOUND, OrderDesk, contact_matches

TODAY = date(2026, 3, 24)


@pytest.mark.parametrize(
    ("contact", "expected"),
    [
        ("anacarol.ferreira@coldmail.com", True),
        ("  AnaCarol.Ferreira@ColdMail.com ", True),
        ("(67) 99654-7821", True),
        ("+55 67 99654-7821", True),
        ("99654-7821", True),
        ("(11) 99654-7821", False),  # same number, other area code
        ("ana@coldmail.com", False),
        ("7821", False),
    ],
)
def test_contact_matching(data, contact, expected):
    assert contact_matches(data.customers[2], contact) is expected


def test_wrong_contact_and_missing_order_look_identical(data):
    desk = OrderDesk(data)
    wrong_contact, failed = desk.lookup(8, "someone@else.com", None, 0, TODAY)
    missing_order, _ = desk.lookup(999, "someone@else.com", None, 0, TODAY)
    assert wrong_contact == missing_order == {"found": False, "message": NOT_FOUND}
    assert failed


def test_attempts_are_limited(data):
    result, failed = OrderDesk(data).lookup(8, "anacarol.ferreira@coldmail.com", None, 3, TODAY)
    assert result["message"] == LOCKED and not failed


def test_verified_lookup_of_a_late_shipment(data):
    result, _ = OrderDesk(data).lookup(8, "(67) 99654-7821", None, 0, TODAY)
    order = result["order"]
    assert order["status"] == "enviado"
    assert order["tracking_code"] == "BRJL5544332BR"
    assert order["delay"] == "previsão de entrega ultrapassada há 24 dia(s)"
    assert order["customer_first_name"] == "Ana"


def test_channel_identified_customer_needs_no_contact(data):
    result, _ = OrderDesk(data).lookup(8, None, verified_customer_id=2, failed_attempts=0, today=TODAY)
    assert result["found"]
    other, _ = OrderDesk(data).lookup(1, None, verified_customer_id=2, failed_attempts=0, today=TODAY)
    assert not other["found"]  # an identified customer still cannot read someone else's order


def test_after_sales_deadlines_for_a_delivered_order(data):
    result, _ = OrderDesk(data).lookup(7, "leticia.rocha@jmail.com", None, 0, TODAY)
    after_sales = result["order"]["after_sales"]
    assert after_sales["days_since_receipt"] == 35
    assert after_sales["regret_return_until"].startswith("24/02/2026")
    assert after_sales["regret_return_until"].endswith("prazo encerrado")
    assert after_sales["defect_exchange_until"].startswith("05/03/2026")
    assert after_sales["legal_warranty_until"].startswith("18/05/2026")
    assert after_sales["legal_warranty_until"].endswith("dentro do prazo")


def test_cancelled_order_shows_the_reason(data):
    result, _ = OrderDesk(data).lookup(18, "gustavo.nunes@inlook.com", None, 0, TODAY)
    assert result["order"]["notes"] == "Item fora de estoque no momento do pedido"


def test_orders_of_identified_customer(data):
    assert [o["order_id"] for o in OrderDesk(data).orders_of(3)] == [19, 1]
