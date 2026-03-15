"""Tests for EventCalendar — macro event schedule and proximity queries.

Run with:  python -m pytest tests/test_event_calendar.py -v --tb=short
All tests are offline (no network calls) and complete in < 5 seconds.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from data.events import EventCalendar, EventType, MarketEvent  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ec():
    """Module-scoped EventCalendar instance (loaded once for all tests)."""
    return EventCalendar()


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------

class TestEventCalendarInstantiation:
    def test_creates_without_error(self):
        cal = EventCalendar()
        assert cal is not None

    def test_has_events_loaded(self, ec):
        events = ec.all_events()
        assert len(events) > 0

    def test_events_sorted_by_date(self, ec):
        events = ec.all_events()
        dates = [e.date for e in events]
        assert dates == sorted(dates), "Events should be sorted by date"

    def test_all_event_types_present(self, ec):
        types = {e.event_type for e in ec.all_events()}
        assert EventType.FOMC in types
        assert EventType.CPI in types
        assert EventType.NFP in types
        assert EventType.OPEX in types
        assert EventType.REBAL in types


# ---------------------------------------------------------------------------
# get_events_on
# ---------------------------------------------------------------------------

class TestGetEventsOn:
    def test_known_fomc_date(self, ec):
        """2026-03-18 is an FOMC meeting day."""
        events = ec.get_events_on(date(2026, 3, 18))
        fomc = [e for e in events if e.event_type == EventType.FOMC]
        assert len(fomc) >= 1, f"Expected FOMC on 2026-03-18, got: {events}"
        assert fomc[0].impact == "high"

    def test_known_cpi_date_2024(self, ec):
        """2024-01-11 is a CPI release date."""
        events = ec.get_events_on(date(2024, 1, 11))
        cpi = [e for e in events if e.event_type == EventType.CPI]
        assert len(cpi) >= 1, f"Expected CPI on 2024-01-11, got: {events}"

    def test_known_cpi_date_2026(self, ec):
        """2026-01-13 is a CPI release date."""
        events = ec.get_events_on(date(2026, 1, 13))
        cpi = [e for e in events if e.event_type == EventType.CPI]
        assert len(cpi) >= 1, f"Expected CPI on 2026-01-13, got: {events}"

    def test_no_events_on_random_date(self, ec):
        """A mid-week date with no scheduled events should return empty."""
        # 2026-03-25 is a Wednesday — not near FOMC (3/18), not typical NFP/CPI
        events_fomc_cpi = [
            e for e in ec.get_events_on(date(2026, 3, 25))
            if e.event_type in (EventType.FOMC, EventType.CPI)
        ]
        assert events_fomc_cpi == []

    def test_returns_list(self, ec):
        result = ec.get_events_on(date(2024, 1, 1))
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# get_events_near
# ---------------------------------------------------------------------------

class TestGetEventsNear:
    def test_fomc_date_2026_03_18_with_window_5(self, ec):
        """get_events_near('2026-03-18', 5) must return the FOMC event."""
        events = ec.get_events_near("2026-03-18", 5)
        fomc = [e for e in events if e.event_type == EventType.FOMC]
        assert len(fomc) >= 1, (
            f"Expected ≥1 FOMC in window around 2026-03-18, got events: {events}"
        )
        fomc_dates = [e.date.isoformat() for e in fomc]
        assert "2026-03-18" in fomc_dates

    def test_accepts_date_object(self, ec):
        """get_events_near should also accept a date object."""
        events = ec.get_events_near(date(2026, 3, 18), 5)
        fomc = [e for e in events if e.event_type == EventType.FOMC]
        assert len(fomc) >= 1

    def test_accepts_datetime_object(self, ec):
        """get_events_near should also accept a datetime object."""
        from datetime import datetime
        events = ec.get_events_near(datetime(2026, 3, 18, 9, 30), 5)
        fomc = [e for e in events if e.event_type == EventType.FOMC]
        assert len(fomc) >= 1

    def test_window_zero_returns_only_exact_date(self, ec):
        events = ec.get_events_near("2026-03-18", 0)
        # With window=0, only events ON that date are returned
        for e in events:
            assert e.date == date(2026, 3, 18)

    def test_invalid_date_string_returns_empty(self, ec):
        result = ec.get_events_near("not-a-date", 5)
        assert result == []

    def test_events_within_window(self, ec):
        """All returned events should be within ±window_days of the centre date."""
        centre = date(2026, 3, 18)
        window = 5
        events = ec.get_events_near("2026-03-18", window)
        for e in events:
            delta = abs((e.date - centre).days)
            assert delta <= window, (
                f"Event {e} is {delta} days from centre, outside window {window}"
            )

    def test_known_cpi_in_window(self, ec):
        """2026-03-11 CPI should appear when querying near 2026-03-13 with window 5."""
        events = ec.get_events_near("2026-03-13", 5)
        cpi = [e for e in events if e.event_type == EventType.CPI]
        assert len(cpi) >= 1


# ---------------------------------------------------------------------------
# get_event_proximity
# ---------------------------------------------------------------------------

class TestGetEventProximity:
    def test_returns_correct_keys(self, ec):
        prox = ec.get_event_proximity(date(2026, 3, 1))
        assert "days_to_fomc" in prox
        assert "days_to_cpi" in prox
        assert "days_to_nfp" in prox
        assert "is_opex_week" in prox

    def test_days_to_fomc_on_fomc_date(self, ec):
        """On the FOMC date itself, days_to_fomc should be 0."""
        prox = ec.get_event_proximity(date(2026, 3, 18))
        assert prox["days_to_fomc"] == 0

    def test_days_to_fomc_day_before(self, ec):
        """One day before FOMC, days_to_fomc should be 1."""
        prox = ec.get_event_proximity(date(2026, 3, 17))
        assert prox["days_to_fomc"] == 1

    def test_days_to_fomc_after_march_18(self, ec):
        """After 2026-03-18, next FOMC is 2026-05-06 (49 days away)."""
        prox = ec.get_event_proximity(date(2026, 3, 19))
        # Next FOMC is 2026-05-06
        expected = (date(2026, 5, 6) - date(2026, 3, 19)).days
        assert prox["days_to_fomc"] == expected

    def test_is_opex_week_true(self, ec):
        """Third Friday of March 2026: 2026-03-20.  Days before should flag opex_week."""
        # March 20, 2026 is the 3rd Friday → OPEX
        prox = ec.get_event_proximity(date(2026, 3, 16))  # 4 days before OPEX
        assert prox["is_opex_week"] == 1

    def test_is_opex_week_false(self, ec):
        """Just after an OPEX date, opex_week should be 0 (next OPEX is far away)."""
        # March 21 is Saturday after OPEX (March 20). Next OPEX is April 17.
        prox = ec.get_event_proximity(date(2026, 3, 21))
        assert prox["is_opex_week"] == 0

    def test_days_nonnegative(self, ec):
        """All days_to_* values should be >= 0 or -1 (not found)."""
        prox = ec.get_event_proximity(date(2026, 1, 1))
        for key in ("days_to_fomc", "days_to_cpi", "days_to_nfp"):
            assert prox[key] >= 0 or prox[key] == -1

    def test_has_days_to_opex_key(self, ec):
        """get_event_proximity must return a days_to_opex key."""
        prox = ec.get_event_proximity(date(2026, 3, 1))
        assert "days_to_opex" in prox

    def test_has_days_to_rebal_key(self, ec):
        """get_event_proximity must return a days_to_rebal key."""
        prox = ec.get_event_proximity(date(2026, 3, 1))
        assert "days_to_rebal" in prox

    def test_days_to_opex_on_opex_date(self, ec):
        """On the OPEX date itself (2026-03-20), days_to_opex should be 0."""
        prox = ec.get_event_proximity(date(2026, 3, 20))
        assert prox["days_to_opex"] == 0

    def test_days_to_opex_before_opex(self, ec):
        """Four days before March 2026 OPEX (2026-03-20), days_to_opex should be 4."""
        prox = ec.get_event_proximity(date(2026, 3, 16))
        assert prox["days_to_opex"] == 4

    def test_days_to_rebal_on_rebal_date(self, ec):
        """On a REBAL date (2026-03-20 is also Q1 REBAL), days_to_rebal should be 0."""
        prox = ec.get_event_proximity(date(2026, 3, 20))
        assert prox["days_to_rebal"] == 0

    def test_days_to_rebal_before_rebal(self, ec):
        """Five days before Q1 2026 REBAL (2026-03-20), days_to_rebal should be 5."""
        prox = ec.get_event_proximity(date(2026, 3, 15))
        assert prox["days_to_rebal"] == 5

    def test_days_to_opex_nonnegative_or_minus_one(self, ec):
        """days_to_opex should be >= 0 or -1 (not found)."""
        prox = ec.get_event_proximity(date(2026, 1, 1))
        assert prox["days_to_opex"] >= 0 or prox["days_to_opex"] == -1

    def test_days_to_rebal_nonnegative_or_minus_one(self, ec):
        """days_to_rebal should be >= 0 or -1 (not found)."""
        prox = ec.get_event_proximity(date(2026, 1, 1))
        assert prox["days_to_rebal"] >= 0 or prox["days_to_rebal"] == -1

    def test_days_to_opex_after_opex_date(self, ec):
        """Day after OPEX (2026-03-21), days_to_opex points to April OPEX (April 17 = 27 days)."""
        prox = ec.get_event_proximity(date(2026, 3, 21))
        expected = (date(2026, 4, 17) - date(2026, 3, 21)).days
        assert prox["days_to_opex"] == expected

    def test_is_opex_week_consistent_with_days_to_opex(self, ec):
        """is_opex_week=1 should coincide with days_to_opex in [0, 6]."""
        for day_offset in range(-10, 30):
            ref = date(2026, 3, 20) + __import__("datetime").timedelta(days=day_offset)
            if ref.year != 2026:
                continue
            prox = ec.get_event_proximity(ref)
            d = prox["days_to_opex"]
            if d != -1 and 0 <= d <= 6:
                assert prox["is_opex_week"] == 1, (
                    f"is_opex_week should be 1 when days_to_opex={d} (ref={ref})"
                )


# ---------------------------------------------------------------------------
# _compute_nfp_dates — first Fridays
# ---------------------------------------------------------------------------

class TestComputeNFPDates:
    def test_nfp_is_first_friday(self, ec):
        """All NFP dates should be the first Friday of their month."""
        nfp_events = ec.events_by_type(EventType.NFP)
        assert len(nfp_events) > 0
        for event in nfp_events:
            d = event.date
            # weekday(): 0=Monday, 4=Friday
            assert d.weekday() == 4, f"{d} is not a Friday (weekday={d.weekday()})"
            # It must be the first Friday: day <= 7
            assert 1 <= d.day <= 7, f"{d} day={d.day} is not the first Friday (expected day 1-7)"

    def test_nfp_every_month(self, ec):
        """There should be exactly one NFP per month over the covered range."""
        nfp_events = ec.events_by_type(EventType.NFP)
        by_month: dict = {}
        for e in nfp_events:
            key = (e.date.year, e.date.month)
            by_month.setdefault(key, []).append(e)
        for key, events in by_month.items():
            assert len(events) == 1, (
                f"Expected 1 NFP for {key}, got {len(events)}: {events}"
            )

    def test_nfp_spans_2020_2026(self, ec):
        nfp_events = ec.events_by_type(EventType.NFP)
        years = {e.date.year for e in nfp_events}
        assert 2020 in years
        assert 2026 in years

    def test_known_nfp_dates(self, ec):
        """Spot-check some known NFP dates (first Fridays)."""
        # January 2026: first Friday is Jan 2
        nfp_jan26 = [e for e in ec.events_by_type(EventType.NFP)
                     if e.date == date(2026, 1, 2)]
        assert len(nfp_jan26) == 1

        # March 2026: first Friday is Mar 6
        nfp_mar26 = [e for e in ec.events_by_type(EventType.NFP)
                     if e.date == date(2026, 3, 6)]
        assert len(nfp_mar26) == 1


# ---------------------------------------------------------------------------
# _compute_opex_dates — third Fridays
# ---------------------------------------------------------------------------

class TestComputeOPEXDates:
    def test_opex_is_third_friday(self, ec):
        """All OPEX dates should be the third Friday of their month."""
        opex_events = ec.events_by_type(EventType.OPEX)
        assert len(opex_events) > 0
        for event in opex_events:
            d = event.date
            assert d.weekday() == 4, f"{d} is not a Friday"
            # Third Friday: day in range [15, 21]
            assert 15 <= d.day <= 21, (
                f"{d} day={d.day} is not the third Friday (expected day 15-21)"
            )

    def test_opex_every_month(self, ec):
        opex_events = ec.events_by_type(EventType.OPEX)
        by_month: dict = {}
        for e in opex_events:
            key = (e.date.year, e.date.month)
            by_month.setdefault(key, []).append(e)
        for key, events in by_month.items():
            assert len(events) == 1

    def test_opex_spans_2020_2026(self, ec):
        opex_events = ec.events_by_type(EventType.OPEX)
        years = {e.date.year for e in opex_events}
        assert 2020 in years
        assert 2026 in years

    def test_known_opex_date_march_2026(self, ec):
        """March 2026 OPEX: third Friday is March 20."""
        opex_mar26 = [e for e in ec.events_by_type(EventType.OPEX)
                      if e.date == date(2026, 3, 20)]
        assert len(opex_mar26) == 1
        assert opex_mar26[0].impact == "medium"

    def test_known_opex_date_january_2024(self, ec):
        """January 2024 OPEX: third Friday is January 19."""
        opex = [e for e in ec.events_by_type(EventType.OPEX)
                if e.date == date(2024, 1, 19)]
        assert len(opex) == 1


# ---------------------------------------------------------------------------
# _compute_rebal_dates — quarterly (third Friday of Mar/Jun/Sep/Dec)
# ---------------------------------------------------------------------------

class TestComputeRebalDates:
    def test_rebal_is_third_friday(self, ec):
        rebal_events = ec.events_by_type(EventType.REBAL)
        assert len(rebal_events) > 0
        for event in rebal_events:
            d = event.date
            assert d.weekday() == 4, f"{d} is not a Friday"
            assert 15 <= d.day <= 21, (
                f"{d} day={d.day} is not the third Friday (expected day 15-21)"
            )

    def test_rebal_only_quarterly_months(self, ec):
        """REBAL events should only be in March, June, September, December."""
        rebal_events = ec.events_by_type(EventType.REBAL)
        for event in rebal_events:
            assert event.date.month in (3, 6, 9, 12), (
                f"REBAL event {event.date} is not in a quarterly month"
            )

    def test_four_rebal_per_year(self, ec):
        """Each year should have exactly 4 rebalancing dates."""
        rebal_events = ec.events_by_type(EventType.REBAL)
        by_year: dict = {}
        for e in rebal_events:
            by_year.setdefault(e.date.year, []).append(e)
        for year, events in by_year.items():
            assert len(events) == 4, (
                f"Expected 4 REBAL dates for {year}, got {len(events)}: {events}"
            )

    def test_rebal_spans_2020_2026(self, ec):
        rebal_events = ec.events_by_type(EventType.REBAL)
        years = {e.date.year for e in rebal_events}
        assert 2020 in years
        assert 2026 in years

    def test_known_rebal_date_march_2026(self, ec):
        """Q1 2026 REBAL: third Friday of March = March 20."""
        rebal = [e for e in ec.events_by_type(EventType.REBAL)
                 if e.date == date(2026, 3, 20)]
        assert len(rebal) == 1
        assert "Q1" in rebal[0].description

    def test_known_rebal_date_dec_2024(self, ec):
        """Q4 2024 REBAL: third Friday of December = December 20."""
        rebal = [e for e in ec.events_by_type(EventType.REBAL)
                 if e.date == date(2024, 12, 20)]
        assert len(rebal) == 1


# ---------------------------------------------------------------------------
# Schedule date range coverage (2020–2026)
# ---------------------------------------------------------------------------

class TestDateRangeCoverage:
    def test_fomc_covers_2020_to_2026(self, ec):
        fomc_events = ec.events_by_type(EventType.FOMC)
        years = {e.date.year for e in fomc_events}
        for year in range(2020, 2027):
            assert year in years, f"No FOMC events found for {year}"

    def test_cpi_covers_2020_to_2026(self, ec):
        cpi_events = ec.events_by_type(EventType.CPI)
        years = {e.date.year for e in cpi_events}
        for year in range(2020, 2027):
            assert year in years, f"No CPI events found for {year}"

    def test_fomc_minimum_count(self, ec):
        """FOMC should have at least 7 meetings per year × 7 years ≥ 49."""
        fomc_events = ec.events_by_type(EventType.FOMC)
        # 2020 has extra emergency meetings, so count > 7 * 7
        assert len(fomc_events) >= 49, (
            f"Expected ≥49 FOMC events, got {len(fomc_events)}"
        )

    def test_cpi_twelve_per_year(self, ec):
        """CPI should have exactly 12 releases per year."""
        cpi_events = ec.events_by_type(EventType.CPI)
        by_year: dict = {}
        for e in cpi_events:
            by_year.setdefault(e.date.year, []).append(e)
        for year, events in by_year.items():
            assert len(events) == 12, (
                f"Expected 12 CPI releases for {year}, got {len(events)}: "
                f"{[e.date.isoformat() for e in events]}"
            )

    def test_all_events_have_valid_dates(self, ec):
        for e in ec.all_events():
            assert isinstance(e.date, date), f"Event {e} has non-date type: {type(e.date)}"
            assert 2020 <= e.date.year <= 2026, f"Event year {e.date.year} out of range"

    def test_all_events_have_valid_impact(self, ec):
        for e in ec.all_events():
            assert e.impact in ("high", "medium", "low"), (
                f"Event {e} has invalid impact '{e.impact}'"
            )


# ---------------------------------------------------------------------------
# MarketEvent dataclass
# ---------------------------------------------------------------------------

class TestMarketEventDataclass:
    def test_repr(self):
        e = MarketEvent(
            event_type=EventType.FOMC,
            date=date(2026, 3, 18),
            description="FOMC Meeting",
            impact="high",
        )
        assert "FOMC" in repr(e)
        assert "2026-03-18" in repr(e)

    def test_default_impact_is_high(self):
        e = MarketEvent(
            event_type=EventType.FOMC,
            date=date(2026, 3, 18),
            description="Test",
        )
        assert e.impact == "high"


# ---------------------------------------------------------------------------
# Verification script (also run as a test)
# ---------------------------------------------------------------------------

def test_verification_script():
    """Reproduce the verification command from the task spec."""
    ec = EventCalendar()
    events = ec.get_events_near("2026-03-18", 5)
    assert len(events) > 0, "No events found near 2026-03-18"
    fomc = [e for e in events if e.event_type == EventType.FOMC]
    assert len(fomc) >= 1, f"Expected FOMC near 2026-03-18, got: {events}"
    # Ensure we can print them (used in the bash verification snippet)
    for e in events:
        line = f"{e.event_type} {e.date} {e.description}"
        assert len(line) > 0
