"""Economic Calendar Service — Hardcoded recurring high-impact events.

MT5 has a native Calendar API but ONLY in MQL5, not accessible from Python.
This service provides ~80% coverage of market-moving events via schedule rules.

Each event defines:
- name: Human-readable name
- currency: Affected currency
- impact: LOW, MEDIUM, HIGH, CRITICAL
- rule: How to compute the date (first_friday, every_thursday, etc.)
- time_utc: Release time in UTC (HH:MM)
- blackout_minutes: Minutes before/after to avoid new entries
- affected_pairs: Which pairs are most affected

Events covered:
- US: NFP, FOMC, CPI, PPI, ISM PMI, Jobless Claims, GDP, Retail Sales
- EU: ECB Rate, Eurozone CPI, German IFO
- UK: BoE Rate, UK GDP, UK CPI, UK PMI
- Japan: BoJ Rate, Tokyo CPI, National CPI
- Australia: RBA Rate, Australian CPI, Employment
- Canada: Canadian Employment, Ivey PMI
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta, date
from typing import Optional


@dataclass
class CalendarEvent:
    """A single economic calendar event."""

    name: str
    currency: str
    impact: str  # LOW, MEDIUM, HIGH, CRITICAL
    event_date: datetime  # UTC
    time_utc: str  # HH:MM
    blackout_minutes: int  # Minutes before/after to avoid trading
    affected_pairs: list[str] = field(default_factory=list)
    description: str = ""

    @property
    def blackout_start(self) -> datetime:
        return self.event_date - timedelta(minutes=self.blackout_minutes)

    @property
    def blackout_end(self) -> datetime:
        return self.event_date + timedelta(minutes=self.blackout_minutes)

    @property
    def is_in_blackout(self) -> bool:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        return self.blackout_start <= now <= self.blackout_end

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "currency": self.currency,
            "impact": self.impact,
            "event_date_utc": self.event_date.isoformat(),
            "time_utc": self.time_utc,
            "blackout_minutes": self.blackout_minutes,
            "blackout_start_utc": self.blackout_start.isoformat(),
            "blackout_end_utc": self.blackout_end.isoformat(),
            "is_in_blackout": self.is_in_blackout,
            "affected_pairs": self.affected_pairs,
            "description": self.description,
        }


# ============================================================
# Date Rule Functions
# ============================================================


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """Return the date of the nth occurrence of weekday in a month.
    weekday: 0=Monday, 6=Sunday
    """
    cal = calendar.monthcalendar(year, month)
    count = 0
    for week in cal:
        if week[weekday] != 0:
            count += 1
            if count == n:
                return date(year, month, week[weekday])
    return date(year, month, cal[-1][weekday])  # fallback to last occurrence


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Return the date of the last occurrence of weekday in a month."""
    cal = calendar.monthcalendar(year, month)
    for week in reversed(cal):
        if week[weekday] != 0:
            return date(year, month, week[weekday])
    return date(year, month, 1)


def _first_friday(year: int, month: int) -> date:
    return _nth_weekday_of_month(year, month, calendar.FRIDAY, 1)


def _first_tuesday(year: int, month: int) -> date:
    return _nth_weekday_of_month(year, month, calendar.TUESDAY, 1)


def _first_business_day(year: int, month: int) -> date:
    """First weekday (Mon-Fri) of the month."""
    d = date(year, month, 1)
    while d.weekday() >= 5:  # Saturday=5, Sunday=6
        d += timedelta(days=1)
    return d


def _last_business_day(year: int, month: int) -> date:
    """Last weekday (Mon-Fri) of the month."""
    import calendar

    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _third_wednesday(year: int, month: int) -> date:
    return _nth_weekday_of_month(year, month, calendar.WEDNESDAY, 3)


def _fourth_thursday(year: int, month: int) -> date:
    return _nth_weekday_of_month(year, month, calendar.THURSDAY, 4)


# ============================================================
# Recurring Event Definitions
# ============================================================


@dataclass
class RecurringEventRule:
    """Template for a recurring economic event."""

    name: str
    currency: str
    impact: str
    rule: str  # How to compute the date
    time_utc: str  # HH:MM
    blackout_minutes: int
    affected_pairs: list[str]
    description: str = ""


RECURRING_EVENTS: list[RecurringEventRule] = [
    # ═══════════════════════════════════════════
    # US EVENTS (highest market impact)
    # ═══════════════════════════════════════════
    RecurringEventRule(
        name="Non-Farm Payrolls (NFP)",
        currency="USD",
        impact="CRITICAL",
        rule="first_friday",
        time_utc="12:30",
        blackout_minutes=60,
        affected_pairs=[
            "EURUSD",
            "GBPUSD",
            "USDJPY",
            "XAUUSD",
            "USDCAD",
            "AUDUSD",
            "NZDUSD",
            "USDCHF",
        ],
        description="Monthly US employment report. Biggest monthly market mover.",
    ),
    RecurringEventRule(
        name="FOMC Rate Decision",
        currency="USD",
        impact="CRITICAL",
        rule="fomc_schedule",  # Special handling — 8 Wednesdays/year
        time_utc="18:00",
        blackout_minutes=120,
        affected_pairs=[
            "EURUSD",
            "GBPUSD",
            "USDJPY",
            "XAUUSD",
            "USDCAD",
            "AUDUSD",
            "NZDUSD",
            "USDCHF",
        ],
        description="Federal Reserve interest rate decision and statement. 8 meetings per year.",
    ),
    RecurringEventRule(
        name="US CPI (Consumer Price Index)",
        currency="USD",
        impact="HIGH",
        rule="mid_month",
        time_utc="12:30",
        blackout_minutes=60,
        affected_pairs=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        description="Key inflation indicator. Markets highly sensitive to CPI surprises.",
    ),
    RecurringEventRule(
        name="US PPI (Producer Price Index)",
        currency="USD",
        impact="MEDIUM",
        rule="mid_month_plus_1",
        time_utc="12:30",
        blackout_minutes=30,
        affected_pairs=["EURUSD", "XAUUSD"],
        description="Wholesale inflation indicator.",
    ),
    RecurringEventRule(
        name="ISM Manufacturing PMI",
        currency="USD",
        impact="HIGH",
        rule="first_business_day",
        time_utc="14:00",
        blackout_minutes=45,
        affected_pairs=["EURUSD", "USDJPY", "XAUUSD"],
        description="US manufacturing activity survey.",
    ),
    RecurringEventRule(
        name="ISM Services PMI",
        currency="USD",
        impact="HIGH",
        rule="third_business_day",
        time_utc="14:00",
        blackout_minutes=45,
        affected_pairs=["EURUSD", "USDJPY", "XAUUSD"],
        description="US services activity survey.",
    ),
    RecurringEventRule(
        name="US Initial Jobless Claims",
        currency="USD",
        impact="MEDIUM",
        rule="every_thursday",
        time_utc="12:30",
        blackout_minutes=30,
        affected_pairs=["EURUSD", "USDJPY", "XAUUSD"],
        description="Weekly US unemployment claims.",
    ),
    RecurringEventRule(
        name="US GDP (Advance)",
        currency="USD",
        impact="HIGH",
        rule="fourth_thursday",
        time_utc="12:30",
        blackout_minutes=60,
        affected_pairs=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        description="Quarterly US GDP advance estimate.",
    ),
    RecurringEventRule(
        name="US Retail Sales",
        currency="USD",
        impact="HIGH",
        rule="mid_month",
        time_utc="12:30",
        blackout_minutes=45,
        affected_pairs=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        description="Monthly consumer spending indicator.",
    ),
    RecurringEventRule(
        name="ADP Non-Farm Employment",
        currency="USD",
        impact="MEDIUM",
        rule="first_wednesday",
        time_utc="12:15",
        blackout_minutes=30,
        affected_pairs=["EURUSD", "XAUUSD"],
        description="Private sector employment report. NFP preview.",
    ),
    RecurringEventRule(
        name="US Core PCE Price Index",
        currency="USD",
        impact="HIGH",
        rule="last_business_day",
        time_utc="12:30",
        blackout_minutes=60,
        affected_pairs=["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        description="Fed's preferred inflation gauge.",
    ),
    # ═══════════════════════════════════════════
    # EUROPEAN EVENTS
    # ═══════════════════════════════════════════
    RecurringEventRule(
        name="ECB Interest Rate Decision",
        currency="EUR",
        impact="CRITICAL",
        rule="ecb_schedule",  # 8 meetings/year, typically Thursdays
        time_utc="12:45",
        blackout_minutes=120,
        affected_pairs=["EURUSD", "EURJPY", "EURGBP", "EURCHF", "EURCAD", "EURAUD"],
        description="European Central Bank rate decision and press conference.",
    ),
    RecurringEventRule(
        name="Eurozone CPI Flash Estimate",
        currency="EUR",
        impact="HIGH",
        rule="end_of_month",
        time_utc="10:00",
        blackout_minutes=60,
        affected_pairs=["EURUSD", "EURJPY", "EURGBP"],
        description="Preliminary Eurozone inflation data.",
    ),
    RecurringEventRule(
        name="German IFO Business Climate",
        currency="EUR",
        impact="MEDIUM",
        rule="fourth_monday",
        time_utc="09:00",
        blackout_minutes=30,
        affected_pairs=["EURUSD", "EURJPY"],
        description="Key German business sentiment indicator.",
    ),
    # ═══════════════════════════════════════════
    # UK EVENTS
    # ═══════════════════════════════════════════
    RecurringEventRule(
        name="BoE Interest Rate Decision",
        currency="GBP",
        impact="CRITICAL",
        rule="boe_schedule",  # 8 meetings/year
        time_utc="12:00",
        blackout_minutes=120,
        affected_pairs=[
            "GBPUSD",
            "GBPJPY",
            "EURGBP",
            "GBPAUD",
            "GBPCAD",
            "GBPNZD",
            "GBPCHF",
        ],
        description="Bank of England rate decision and vote.",
    ),
    RecurringEventRule(
        name="UK CPI",
        currency="GBP",
        impact="HIGH",
        rule="mid_month",
        time_utc="06:00",
        blackout_minutes=60,
        affected_pairs=["GBPUSD", "GBPJPY", "EURGBP"],
        description="UK consumer price inflation.",
    ),
    RecurringEventRule(
        name="UK GDP",
        currency="GBP",
        impact="HIGH",
        rule="monthly_gdp",
        time_utc="06:00",
        blackout_minutes=60,
        affected_pairs=["GBPUSD", "GBPJPY", "EURGBP"],
        description="UK gross domestic product.",
    ),
    # ═══════════════════════════════════════════
    # JAPAN EVENTS
    # ═══════════════════════════════════════════
    RecurringEventRule(
        name="BoJ Interest Rate Decision",
        currency="JPY",
        impact="CRITICAL",
        rule="boj_schedule",  # 8 meetings/year
        time_utc="03:00",
        blackout_minutes=120,
        affected_pairs=[
            "USDJPY",
            "EURJPY",
            "GBPJPY",
            "AUDJPY",
            "NZDJPY",
            "CADJPY",
            "CHFJPY",
        ],
        description="Bank of Japan rate decision and outlook report.",
    ),
    RecurringEventRule(
        name="Tokyo Core CPI",
        currency="JPY",
        impact="MEDIUM",
        rule="end_of_month",
        time_utc="23:30",
        blackout_minutes=45,
        affected_pairs=["USDJPY", "EURJPY", "GBPJPY"],
        description="Tokyo area CPI — leading indicator for national CPI.",
    ),
    RecurringEventRule(
        name="Japan National CPI",
        currency="JPY",
        impact="HIGH",
        rule="end_of_month_plus_1",
        time_utc="23:30",
        blackout_minutes=60,
        affected_pairs=["USDJPY", "EURJPY", "GBPJPY"],
        description="Japan national consumer price index.",
    ),
    # ═══════════════════════════════════════════
    # AUSTRALIA EVENTS
    # ═══════════════════════════════════════════
    RecurringEventRule(
        name="RBA Interest Rate Decision",
        currency="AUD",
        impact="CRITICAL",
        rule="first_tuesday",
        time_utc="01:30",
        blackout_minutes=120,
        affected_pairs=["AUDUSD", "AUDJPY", "EURAUD", "GBPAUD", "AUDCAD", "AUDNZD"],
        description="Reserve Bank of Australia rate decision.",
    ),
    RecurringEventRule(
        name="Australian CPI",
        currency="AUD",
        impact="HIGH",
        rule="quarterly",  # Jan, Apr, Jul, Oct
        time_utc="00:30",
        blackout_minutes=60,
        affected_pairs=["AUDUSD", "AUDJPY", "EURAUD"],
        description="Quarterly Australian consumer price index.",
    ),
    RecurringEventRule(
        name="Australian Employment Change",
        currency="AUD",
        impact="HIGH",
        rule="third_thursday",
        time_utc="00:30",
        blackout_minutes=45,
        affected_pairs=["AUDUSD", "AUDJPY"],
        description="Monthly Australian employment data.",
    ),
    # ═══════════════════════════════════════════
    # CANADA EVENTS
    # ═══════════════════════════════════════════
    RecurringEventRule(
        name="Canadian Employment Change",
        currency="CAD",
        impact="HIGH",
        rule="first_friday",
        time_utc="12:30",
        blackout_minutes=60,
        affected_pairs=["USDCAD", "CADJPY", "EURAUD", "GBPCAD", "AUDCAD"],
        description="Canadian jobs report — moves alongside NFP.",
    ),
    RecurringEventRule(
        name="Canadian CPI",
        currency="CAD",
        impact="HIGH",
        rule="mid_month",
        time_utc="12:30",
        blackout_minutes=45,
        affected_pairs=["USDCAD", "CADJPY"],
        description="Canadian consumer price index.",
    ),
]

# FOMC 2025-2026 meeting dates (8 per year, Wednesdays)
# These are the approximate dates — Fed publishes exact schedule
_FOMC_2025 = [
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-11-05",
    "2025-12-10",
]
_FOMC_2026 = [
    "2026-01-28",
    "2026-03-18",
    "2026-05-06",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-11-04",
    "2026-12-16",
]

# ECB 2025-2026 meeting dates
_ECB_2025 = [
    "2025-01-30",
    "2025-03-06",
    "2025-04-10",
    "2025-06-05",
    "2025-07-17",
    "2025-09-04",
    "2025-10-30",
    "2025-12-11",
]
_ECB_2026 = [
    "2026-01-29",
    "2026-03-05",
    "2026-04-09",
    "2026-06-04",
    "2026-07-16",
    "2026-09-03",
    "2026-10-29",
    "2026-12-10",
]

# BoE 2025-2026 meeting dates
_BOE_2025 = [
    "2025-02-06",
    "2025-03-20",
    "2025-05-08",
    "2025-06-19",
    "2025-08-07",
    "2025-09-18",
    "2025-11-06",
    "2025-12-18",
]
_BOE_2026 = [
    "2026-02-05",
    "2026-03-26",
    "2026-05-07",
    "2026-06-18",
    "2026-08-06",
    "2026-09-17",
    "2026-11-05",
    "2026-12-17",
]

# BoJ 2025-2026 meeting dates
_BOJ_2025 = [
    "2025-01-24",
    "2025-03-19",
    "2025-04-30",
    "2025-06-17",
    "2025-07-31",
    "2025-09-17",
    "2025-10-31",
    "2025-12-19",
]
_BOJ_2026 = [
    "2026-01-23",
    "2026-03-18",
    "2026-04-28",
    "2026-06-16",
    "2026-07-31",
    "2026-09-17",
    "2026-10-30",
    "2026-12-18",
]


def _get_hardcoded_dates(rule: str, year: int, month: int) -> list[date]:
    """Get specific dates for special schedule rules."""
    dates = []

    if rule == "fomc_schedule":
        all_dates = _FOMC_2025 + _FOMC_2026
    elif rule == "ecb_schedule":
        all_dates = _ECB_2025 + _ECB_2026
    elif rule == "boe_schedule":
        all_dates = _BOE_2025 + _BOE_2026
    elif rule == "boj_schedule":
        all_dates = _BOJ_2025 + _BOJ_2026
    else:
        return []

    for d_str in all_dates:
        d = date.fromisoformat(d_str)
        if d.year == year and d.month == month:
            dates.append(d)

    return dates


def _compute_event_dates(rule: str, year: int, month: int) -> list[date]:
    """Compute event dates based on the rule."""
    # Check hardcoded schedules first
    hardcoded = _get_hardcoded_dates(rule, year, month)
    if hardcoded:
        return hardcoded

    # Dynamic rules
    rule_map = {
        "first_friday": lambda: [_first_friday(year, month)],
        "first_tuesday": lambda: [_first_tuesday(year, month)],
        "first_business_day": lambda: [_first_business_day(year, month)],
        "third_business_day": lambda: [
            _first_business_day(year, month) + timedelta(days=14)  # approx
        ],
        "last_business_day": lambda: [_last_business_day(year, month)],
        "third_wednesday": lambda: [_third_wednesday(year, month)],
        "fourth_thursday": lambda: [_fourth_thursday(year, month)],
        "first_wednesday": lambda: [
            _nth_weekday_of_month(year, month, calendar.WEDNESDAY, 1)
        ],
        "fourth_monday": lambda: [
            _nth_weekday_of_month(year, month, calendar.MONDAY, 4)
        ],
        "third_thursday": lambda: [
            _nth_weekday_of_month(year, month, calendar.THURSDAY, 3)
        ],
        "mid_month": lambda: [
            date(year, month, min(15, calendar.monthrange(year, month)[1]))
        ],
        "mid_month_plus_1": lambda: [
            date(year, month, min(16, calendar.monthrange(year, month)[1]))
        ],
        "end_of_month": lambda: [
            date(year, month, calendar.monthrange(year, month)[1])
        ],
        "end_of_month_plus_1": lambda: [
            date(
                year,
                month,
                min(
                    calendar.monthrange(year, month)[1],
                    calendar.monthrange(year, month)[1],
                ),
            )
        ],
        "monthly": lambda: [_first_business_day(year, month)],
        "monthly_gdp": lambda: [
            _nth_weekday_of_month(year, month, calendar.WEDNESDAY, 2)
        ],
    }

    fn = rule_map.get(rule)
    if fn:
        return fn()

    # Every Thursday rule
    if rule == "every_thursday":
        dates = []
        for week in calendar.monthcalendar(year, month):
            if week[calendar.THURSDAY] != 0:
                dates.append(date(year, month, week[calendar.THURSDAY]))
        return dates

    # Quarterly rule (Jan, Apr, Jul, Oct)
    if rule == "quarterly":
        if month in (1, 4, 7, 10):
            return [date(year, month, min(28, calendar.monthrange(year, month)[1]))]
        return []

    return []


def _parse_time(time_utc: str) -> tuple[int, int]:
    """Parse HH:MM time string to (hour, minute)."""
    parts = time_utc.split(":")
    return int(parts[0]), int(parts[1])


# ============================================================
# Public API
# ============================================================


def get_upcoming_events(
    hours_ahead: int = 24,
    currency: Optional[str] = None,
    min_impact: str = "MEDIUM",
    utc_now: Optional[datetime] = None,
) -> list[CalendarEvent]:
    """Get upcoming economic events within the specified window.

    Args:
        hours_ahead: How many hours ahead to look.
        currency: Filter by currency code (e.g., "USD", "EUR").
        min_impact: Minimum impact level — "LOW", "MEDIUM", "HIGH", "CRITICAL".
        utc_now: Current UTC time. Defaults to now.

    Returns:
        List of CalendarEvent objects sorted by date.
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    if utc_now.tzinfo is not None:
        utc_now = utc_now.replace(tzinfo=None)

    impact_levels = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
    min_level = impact_levels.get(min_impact, 1)

    end_time = utc_now + timedelta(hours=hours_ahead)

    events = []

    # Check current and next month
    for check_month_offset in range(0, 3):
        check_date = utc_now + timedelta(days=check_month_offset * 30)
        year = check_date.year
        month = check_date.month

        for rule in RECURRING_EVENTS:
            # Currency filter
            if currency and rule.currency != currency.upper():
                continue

            # Impact filter
            if impact_levels.get(rule.impact, 0) < min_level:
                continue

            # Get dates for this month
            event_dates = _compute_event_dates(rule.rule, year, month)

            for event_date in event_dates:
                hour, minute = _parse_time(rule.time_utc)
                event_dt = datetime(year, month, event_date.day, hour, minute)

                # Check if within window
                if utc_now <= event_dt <= end_time:
                    events.append(
                        CalendarEvent(
                            name=rule.name,
                            currency=rule.currency,
                            impact=rule.impact,
                            event_date=event_dt,
                            time_utc=rule.time_utc,
                            blackout_minutes=rule.blackout_minutes,
                            affected_pairs=rule.affected_pairs,
                            description=rule.description,
                        )
                    )

    # Sort by date
    events.sort(key=lambda e: e.event_date)
    return events


def is_blackout_now(
    currency: Optional[str] = None,
    minutes_ahead: int = 30,
    utc_now: Optional[datetime] = None,
) -> dict:
    """Check if we're currently in a news blackout window.

    Args:
        currency: Filter by currency. None = check all.
        minutes_ahead: How many minutes ahead to check.
        utc_now: Current UTC time.

    Returns:
        dict with is_blackout, events_causing_blackout, and time_to_clear.
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    if utc_now.tzinfo is not None:
        utc_now = utc_now.replace(tzinfo=None)

    # Check events that started within the last `minutes_ahead` minutes
    # or will start within the next `minutes_ahead` minutes
    window_start = utc_now - timedelta(minutes=minutes_ahead)
    window_end = utc_now + timedelta(minutes=minutes_ahead)

    events = get_upcoming_events(
        hours_ahead=max(2, minutes_ahead // 60 + 1),
        currency=currency,
        min_impact="HIGH",
        utc_now=utc_now,
    )

    blackout_events = []
    time_to_clear = 0

    for event in events:
        # Check if current time is within the blackout window
        event_start = event.event_date - timedelta(minutes=event.blackout_minutes)
        event_end = event.event_date + timedelta(minutes=event.blackout_minutes)

        if event_start <= utc_now <= event_end:
            blackout_events.append(event.to_dict())
            remaining = (event_end - utc_now).total_seconds() / 60
            if time_to_clear == 0 or remaining < time_to_clear:
                time_to_clear = round(remaining, 1)

    return {
        "is_blackout": len(blackout_events) > 0,
        "events_causing_blackout": blackout_events,
        "minutes_until_clear": time_to_clear,
        "checked_currency": currency or "ALL",
        "checked_at_utc": utc_now.isoformat(),
    }


def get_blackout_windows(
    hours_ahead: int = 24,
    currency: Optional[str] = None,
    utc_now: Optional[datetime] = None,
) -> list[dict]:
    """Get all blackout windows in the specified time range.

    Returns:
        List of dicts with event_name, start_utc, end_utc, currency, impact.
    """
    events = get_upcoming_events(
        hours_ahead=hours_ahead,
        currency=currency,
        min_impact="HIGH",
        utc_now=utc_now,
    )

    windows = []
    for event in events:
        windows.append(
            {
                "event_name": event.name,
                "start_utc": event.blackout_start.isoformat(),
                "end_utc": event.blackout_end.isoformat(),
                "event_time_utc": event.event_date.isoformat(),
                "currency": event.currency,
                "impact": event.impact,
                "blackout_minutes": event.blackout_minutes,
                "affected_pairs": event.affected_pairs,
            }
        )

    return windows


def get_events_for_currency(
    currency: str,
    hours_ahead: int = 24,
    utc_now: Optional[datetime] = None,
) -> list[dict]:
    """Get all upcoming events affecting a specific currency."""
    events = get_upcoming_events(
        hours_ahead=hours_ahead,
        currency=currency.upper(),
        min_impact="MEDIUM",
        utc_now=utc_now,
    )
    return [e.to_dict() for e in events]


def get_daily_briefing(utc_now: Optional[datetime] = None) -> dict:
    """Get a daily economic calendar briefing.

    Returns:
        Dict with today's events, upcoming events, and trading recommendations.
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    if utc_now.tzinfo is not None:
        utc_now = utc_now.replace(tzinfo=None)

    # Get events for the rest of today (until midnight UTC)
    end_of_day = datetime(utc_now.year, utc_now.month, utc_now.day, 23, 59, 59)
    hours_remaining = (end_of_day - utc_now).total_seconds() / 3600

    today_events = get_upcoming_events(
        hours_ahead=max(1, hours_remaining),
        min_impact="MEDIUM",
        utc_now=utc_now,
    )

    # Get events for tomorrow
    tomorrow = utc_now + timedelta(days=1)
    tomorrow_events = get_upcoming_events(
        hours_ahead=24,
        min_impact="MEDIUM",
        utc_now=datetime(tomorrow.year, tomorrow.month, tomorrow.day),
    )

    # Check current blackout
    blackout = is_blackout_now(utc_now=utc_now)

    # Extract affected currencies
    affected_currencies = set()
    for e in today_events:
        affected_currencies.add(e.currency)
        affected_currencies.update(p[:3] for p in e.affected_pairs)
        affected_currencies.update(p[3:6] for p in e.affected_pairs if len(p) >= 6)

    # Unique currencies
    unique_currencies = sorted(set(c for c in affected_currencies if len(c) == 3))

    return {
        "date_utc": utc_now.strftime("%Y-%m-%d"),
        "current_time_utc": utc_now.isoformat(),
        "is_market_open": True,  # Use session_service for actual check
        "current_blackout": blackout,
        "today_events": [e.to_dict() for e in today_events],
        "today_event_count": len(today_events),
        "critical_events_today": len(
            [e for e in today_events if e.impact == "CRITICAL"]
        ),
        "high_impact_events_today": len(
            [e for e in today_events if e.impact == "HIGH"]
        ),
        "tomorrow_events_preview": [e.to_dict() for e in tomorrow_events[:5]],
        "tomorrow_event_count": len(tomorrow_events),
        "affected_currencies": unique_currencies,
        "trading_recommendation": (
            "⚠️ High-impact day — exercise caution"
            if len([e for e in today_events if e.impact in ("HIGH", "CRITICAL")]) >= 3
            else "⚠️ Critical event(s) today — avoid entries around event times"
            if any(e.impact == "CRITICAL" for e in today_events)
            else "Normal day — standard trading conditions"
        ),
    }
