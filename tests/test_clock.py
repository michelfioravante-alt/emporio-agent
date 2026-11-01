from datetime import datetime

import pytest

from app.clock import STORE_TZ, is_open, store_context


def at(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=STORE_TZ)


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        ("2026-11-02 08:59", False),  # Monday before opening
        ("2026-11-02 09:00", True),
        ("2026-11-02 17:59", True),
        ("2026-11-02 18:00", False),
        ("2026-11-07 12:59", True),  # Saturday
        ("2026-11-07 13:00", False),
        ("2026-11-08 10:00", False),  # Sunday
    ],
)
def test_opening_hours(moment, expected):
    assert is_open(at(moment)) is expected


def test_context_points_to_next_opening():
    assert "segunda-feira às 09:00" in store_context(at("2026-11-07 14:00"))
    assert "sábado às 09:00" in store_context(at("2026-11-06 19:00"))
    assert "ABERTA até 18:00" in store_context(at("2026-11-03 10:00"))
