from datetime import datetime

import pytest

from emporio.config import STORE_TZ
from emporio.store import holiday_name, is_open, next_opening, store_status, turn_context


def test_manual_is_split_into_its_ten_sections(policies):
    assert [s.number for s in policies.sections] == list(range(1, 11))
    assert policies.number(4).title == "Política de Trocas e Devoluções"


def test_staff_only_sections_are_not_exposed(policies):
    assert [s.number for s in policies.customer_sections] == [1, 2, 3, 4, 5, 6, 8, 9]


@pytest.mark.parametrize(
    ("number", "fact"),
    [
        (2, "Sábado | 09:00 às 13:00"),
        (3, "PIX | Pagamento à vista | 5% de desconto sobre o preço de tabela"),
        (3, "Parcelamento de 7x a 12x: sem juros, parcela mínima de R$ 100,00."),
        (
            4,
            "- O cliente pode solicitar a devolução em até 7 (sete) dias corridos após o recebimento "
            "do produto, sem necessidade de justificativa.",
        ),
        (5, "Para pedidos abaixo de R$ 500,00, taxa fixa de R$ 35,00."),
        (5, "SEDEX (Correios) | 2 a 5 dias úteis"),  # table that continues on the next page
        (8, "garantia legal de 90 (noventa) dias"),
    ],
)
def test_layout_text_is_reflowed_into_readable_lines(policies, number, fact):
    assert fact in policies.number(number).text


def test_page_furniture_is_removed_and_errata_applied(policies):
    full = "\n".join(s.text for s in policies.sections)
    assert "Página" not in full and "Manual de Políticas e Procedimentos" not in full
    assert "contato@emporiodamusica.com.br" in policies.number(1).text
    assert "empóriodamusica" not in full


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=STORE_TZ)


def test_opening_hours():
    assert is_open(at("2026-03-24T10:30"))  # Tuesday
    assert not is_open(at("2026-03-24T18:00"))
    assert is_open(at("2026-03-28T12:59"))  # Saturday
    assert not is_open(at("2026-03-28T13:00"))
    assert next_opening(at("2026-03-28T13:00")) == at("2026-03-30T09:00")  # skips Sunday


def test_holidays_close_the_store():
    assert holiday_name(at("2026-04-21T10:00").date())  # Tiradentes, a Tuesday
    assert not is_open(at("2026-04-21T10:00"))
    assert "feriado" in store_status(at("2026-04-21T10:00"))


def test_turn_context_mentions_time_status_and_customer():
    context = turn_context(at("2026-03-22T20:00"), "Ana Carolina Ferreira")
    assert "domingo, 22/03/2026, 20:00" in context
    assert "volta segunda-feira, 23/03/2026, às 09:00" in context
    assert "Ana Carolina Ferreira" in context
