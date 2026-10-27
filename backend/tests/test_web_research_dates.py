"""Shanghai midnight must not move a US research run into tomorrow."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime

from tradingagents.dataflows import date_window
from tradingagents.web.domain.research_dates import current_research_date


def test_us_date_is_independent_of_shanghai_midnight_and_observes_dst():
    assert current_research_date(datetime(2026, 10, 25, 16, 30, tzinfo=UTC)) == "2026-10-25"
    assert current_research_date(datetime(2026, 11, 2, 4, 30, tzinfo=UTC)) == "2026-11-01"
    assert current_research_date(datetime(2026, 11, 2, 5, 30, tzinfo=UTC)) == "2026-11-02"


def test_worker_scope_uses_us_present_and_restores_legacy_date(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = datetime(2026, 10, 25, 16, 30, tzinfo=UTC)
            return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)

    class HostDate(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 26)

    monkeypatch.setattr(date_window, "datetime", Clock)
    monkeypatch.setattr(date_window, "date", HostDate)
    assert date_window.get_current_date() == "2026-10-26"
    with date_window.date_timezone_scope("America/New_York"):
        assert date_window.get_current_date() == "2026-10-25"
        assert not date_window.is_historical("2026-10-25")
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(date_window.get_current_date).result() == "2026-10-26"
    assert date_window.get_current_date() == "2026-10-26"
