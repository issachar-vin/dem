from datetime import datetime, timezone

from conductor.localtime import format_display, to_display


def test_naive_is_treated_as_utc_and_converted_to_eastern() -> None:
    # 2026-07-11 12:00 UTC is summer → EDT (UTC-4).
    dt = datetime(2026, 7, 11, 12, 0, 0)
    assert format_display(dt) == "2026-07-11 08:00 EDT"


def test_winter_date_renders_est() -> None:
    # January → EST (UTC-5).
    dt = datetime(2026, 1, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert format_display(dt) == "2026-01-11 07:00 EST"


def test_aware_input_is_converted_not_reinterpreted() -> None:
    dt = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert to_display(dt).utcoffset().total_seconds() == -4 * 3600
