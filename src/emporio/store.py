"""Opening hours (policy section 2) and the per-turn context given to the agent."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import lru_cache

import holidays

# §2: Monday-Friday 09:00-18:00, Saturday 09:00-13:00, closed on Sundays and holidays.
OPENING_HOURS = {weekday: (time(9), time(18)) for weekday in range(5)} | {5: (time(9), time(13))}
WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


@lru_cache(maxsize=8)
def _holidays(year: int) -> holidays.HolidayBase:
    # National and Mato Grosso do Sul holidays. Municipal holidays of Campo Grande
    # are not covered by the library (README, Limitations).
    return holidays.country_holidays("BR", subdiv="MS", years=year, language="pt_BR")


def holiday_name(day: date) -> str | None:
    return _holidays(day.year).get(day)


def hours_on(day: date) -> tuple[time, time] | None:
    if holiday_name(day):
        return None
    return OPENING_HOURS.get(day.weekday())


def is_open(now: datetime) -> bool:
    hours = hours_on(now.date())
    return bool(hours) and hours[0] <= now.time() < hours[1]


def next_opening(now: datetime) -> datetime:
    for offset in range(0, 15):
        day = now.date() + timedelta(days=offset)
        hours = hours_on(day)
        if hours is None:
            continue
        opening = datetime.combine(day, hours[0], tzinfo=now.tzinfo)
        if opening > now:
            return opening
    raise RuntimeError("No opening found in the next two weeks")  # pragma: no cover


def describe_day(moment: datetime) -> str:
    return f"{WEEKDAYS[moment.weekday()]}, {moment:%d/%m/%Y}"


def store_status(now: datetime) -> str:
    holiday = holiday_name(now.date())
    if is_open(now):
        closing = hours_on(now.date())[1]
        return f"aberta agora (hoje até {closing:%H:%M})"
    reopening = next_opening(now)
    reason = f"feriado: {holiday}" if holiday else "fora do horário de funcionamento"
    return f"fechada agora ({reason}); a equipe volta {describe_day(reopening)}, às {reopening:%H:%M}"


def turn_context(now: datetime, customer_name: str | None) -> str:
    """Operator-provided facts for this turn, sent as a system message."""
    customer = (
        f"Cliente identificado pelo número do WhatsApp: {customer_name}"
        if customer_name
        else "Cliente não identificado"
    )
    return (
        "[Contexto do sistema para este turno]\n"
        f"- Agora em Campo Grande/MS: {describe_day(now)}, {now:%H:%M}.\n"
        f"- Loja e equipe humana: {store_status(now)}.\n"
        f"- {customer}."
    )
