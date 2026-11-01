"""Store-local time and opening hours (policy section 2).

Holidays are not modelled: the manual lists no calendar, so the agent
is told to mention that holidays may change the schedule.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

STORE_TZ = ZoneInfo("America/Campo_Grande")

WEEKDAYS_PT = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# weekday() -> (open, close); Sunday closed.
OPENING_HOURS: dict[int, tuple[time, time]] = {
    **{day: (time(9), time(18)) for day in range(5)},
    5: (time(9), time(13)),
}


def now_local() -> datetime:
    return datetime.now(STORE_TZ)


def is_open(moment: datetime) -> bool:
    hours = OPENING_HOURS.get(moment.weekday())
    return bool(hours) and hours[0] <= moment.time() < hours[1]


def next_opening(moment: datetime) -> datetime:
    for offset in range(8):
        day = (moment + timedelta(days=offset)).date()
        hours = OPENING_HOURS.get(day.weekday())
        if not hours:
            continue
        opening = datetime.combine(day, hours[0], tzinfo=STORE_TZ)
        if opening > moment:
            return opening
    raise RuntimeError("opening hours misconfigured")


def store_context(moment: datetime | None = None) -> str:
    moment = moment or now_local()
    weekday = WEEKDAYS_PT[moment.weekday()]
    line = f"Agora: {weekday}, {moment:%d/%m/%Y %H:%M} (horário de Campo Grande/MS)."
    if is_open(moment):
        close = OPENING_HOURS[moment.weekday()][1]
        return f"{line} A loja está ABERTA até {close:%H:%M}."
    opening = next_opening(moment)
    return (
        f"{line} A loja está FECHADA agora. Próxima abertura: "
        f"{WEEKDAYS_PT[opening.weekday()]} às {opening:%H:%M}."
    )
