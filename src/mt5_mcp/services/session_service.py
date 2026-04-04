"""Forex Trading Session Awareness Service.

Determines active trading sessions, overlaps, DST transitions,
session quality scores for pairs, and day-of-week volatility factors.

All calculations are in UTC. Zero external dependencies.

Sessions (UTC):
- Sydney:  21:00-06:00 (winter) / 22:00-07:00 (summer, AU DST)
- Tokyo:   00:00-09:00 (no DST in Japan)
- London:  08:00-17:00 (winter GMT) / 07:00-16:00 (summer BST)
- New York: 13:00-22:00 (winter EST) / 12:00-21:00 (summer EDT)

Key overlaps:
- Tokyo-London: 07:00-09:00 UTC (summer) / 08:00-09:00 UTC (winter) — 1 hour
- London-New York: 12:00-16:00 UTC (summer) / 13:00-17:00 UTC (winter) — 4 hours

Market week: Opens Sunday 22:00 UTC, closes Friday 22:00 UTC.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional


# ============================================================
# DST Detection — no pytz/zoneinfo needed for our use case
# ============================================================


def _nth_sunday_of_month(year: int, month: int, n: int) -> int:
    """Return the day-of-month of the nth Sunday."""
    cal = calendar.monthcalendar(year, month)
    count = 0
    for week in cal:
        if week[calendar.SUNDAY] != 0:
            count += 1
            if count == n:
                return week[calendar.SUNDAY]
    # Fallback: last Sunday
    return _last_sunday_of_month(year, month)


def _last_sunday_of_month(year: int, month: int) -> int:
    """Return the day-of-month of the last Sunday."""
    import calendar

    # Get the last day of the month
    last_day = calendar.monthrange(year, month)[1]
    # Create a calendar matrix
    cal = calendar.monthcalendar(year, month)
    # Last week's Sunday (index 6)
    last_week = cal[-1]
    sunday = last_week[calendar.SUNDAY]
    if sunday == 0:
        # Sunday falls in the previous week
        sunday = cal[-2][calendar.SUNDAY]
    return sunday


def is_us_dst(dt: datetime) -> bool:
    """US DST: 2nd Sunday of March → 1st Sunday of November."""
    year = dt.year
    march_2nd_sun = datetime(year, 3, _nth_sunday_of_month(year, 3, 2))
    nov_1st_sun = datetime(year, 11, _nth_sunday_of_month(year, 11, 1))
    return march_2nd_sun <= dt < nov_1st_sun


def is_eu_dst(dt: datetime) -> bool:
    """EU/UK DST: Last Sunday of March → Last Sunday of October."""
    year = dt.year
    march_last_sun = datetime(year, 3, _last_sunday_of_month(year, 3))
    oct_last_sun = datetime(year, 10, _last_sunday_of_month(year, 10))
    return march_last_sun <= dt < oct_last_sun


def is_au_dst(dt: datetime) -> bool:
    """Australia (Sydney) DST: 1st Sunday of October → 1st Sunday of April.

    Note: Southern hemisphere — opposite to northern hemisphere.
    """
    year = dt.year
    month = dt.month

    # DST starts 1st Sunday of October
    oct_1st_sun = _nth_sunday_of_month(year, 10, 1)
    # DST ends 1st Sunday of April
    apr_1st_sun = _nth_sunday_of_month(year, 4, 1)

    # October-December: DST active from 1st Sunday of October
    if month >= 10:
        oct_start = datetime(year, 10, oct_1st_sun)
        return dt >= oct_start

    # January-April: DST active until 1st Sunday of April
    if month <= 4:
        apr_end = datetime(year, 4, apr_1st_sun)
        return dt < apr_end

    # May-September: no DST
    return False


# ============================================================
# Session Definitions
# ============================================================


@dataclass
class SessionWindow:
    """A named time window in UTC."""

    name: str
    start_hour: int
    start_minute: int
    end_hour: int
    end_minute: int
    is_active: bool = False

    @property
    def start_minutes(self) -> int:
        return self.start_hour * 60 + self.start_minute

    @property
    def end_minutes(self) -> int:
        return self.end_hour * 60 + self.end_minute

    @property
    def duration_minutes(self) -> int:
        if self.end_minutes <= self.start_minutes:
            return (24 * 60 - self.start_minutes) + self.end_minutes
        return self.end_minutes - self.start_minutes


@dataclass
class SessionContext:
    """Complete session context for a given UTC time."""

    utc_now: datetime
    current_sessions: list[str] = field(default_factory=list)
    active_overlaps: list[str] = field(default_factory=list)
    is_market_open: bool = True
    time_to_next_session: int = 0  # minutes
    time_to_session_close: int = 0  # minutes
    volatility_regime: str = "normal"  # low, medium, high, extreme
    spread_quality: str = "normal"  # wide, normal, tight
    volume_concentration: float = 0.0  # 0-1, how much of daily volume is active
    session_quality_scores: dict[str, float] = field(default_factory=dict)
    day_of_week: str = ""
    day_of_week_factor: float = 1.0
    warnings: list[str] = field(default_factory=list)
    recommended_pairs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "utc_now": self.utc_now.isoformat(),
            "current_sessions": self.current_sessions,
            "active_overlaps": self.active_overlaps,
            "is_market_open": self.is_market_open,
            "time_to_next_session_minutes": self.time_to_next_session,
            "time_to_session_close_minutes": self.time_to_session_close,
            "volatility_regime": self.volatility_regime,
            "spread_quality": self.spread_quality,
            "volume_concentration_pct": round(self.volume_concentration * 100, 1),
            "session_quality_scores": self.session_quality_scores,
            "day_of_week": self.day_of_week,
            "day_of_week_factor": self.day_of_week_factor,
            "warnings": self.warnings,
            "recommended_pairs": self.recommended_pairs,
        }


# ============================================================
# Pair-Session Mapping
# ============================================================

PAIR_SESSION_PREFERENCE: dict[str, list[str]] = {
    "EURUSD": ["london", "new_york", "london_ny_overlap"],
    "GBPUSD": ["london", "new_york", "london_ny_overlap"],
    "USDJPY": ["tokyo", "london", "new_york"],
    "EURJPY": ["tokyo", "london", "tokyo_london_overlap"],
    "GBPJPY": ["london", "tokyo", "tokyo_london_overlap"],
    "AUDUSD": ["sydney", "tokyo", "london"],
    "AUDJPY": ["sydney", "tokyo", "london"],
    "NZDUSD": ["sydney", "tokyo", "london"],
    "NZDJPY": ["sydney", "tokyo"],
    "USDCAD": ["new_york", "london"],
    "CADJPY": ["tokyo", "new_york"],
    "USDCHF": ["london", "new_york"],
    "EURGBP": ["london"],
    "EURCHF": ["london"],
    "GBPCHF": ["london"],
    "XAUUSD": ["london", "new_york", "london_ny_overlap"],
    "XAGUSD": ["london", "new_york", "london_ny_overlap"],
    "BTCUSD": [],  # 24/7, no session preference
    "ETHUSD": [],  # 24/7, no session preference
}

# Day-of-week volatility factors (relative to weekly average)
DAY_OF_WEEK_FACTORS: dict[str, float] = {
    "Monday": 0.75,  # Quietest, range-bound
    "Tuesday": 1.0,
    "Wednesday": 1.05,
    "Thursday": 1.15,  # Most volatile
    "Friday": 0.9,  # Afternoon decline
    "Saturday": 0.0,  # Market closed
    "Sunday": 0.3,  # Opens 22:00 UTC
}

# Session volatility profiles
SESSION_VOLATILITY: dict[str, str] = {
    "sydney": "low",
    "tokyo": "medium",
    "london": "high",
    "new_york": "high",
    "london_ny_overlap": "extreme",
    "tokyo_london_overlap": "high",
    "sydney_tokyo_overlap": "low",
}

SESSION_SPREAD_QUALITY: dict[str, str] = {
    "sydney": "wide",
    "tokyo": "normal",
    "london": "tight",
    "new_york": "tight",
    "london_ny_overlap": "tight",
    "tokyo_london_overlap": "normal",
    "sydney_tokyo_overlap": "wide",
}

# Estimated volume concentration per session (% of daily volume)
SESSION_VOLUME: dict[str, float] = {
    "sydney": 0.03,
    "tokyo": 0.10,
    "london": 0.35,
    "new_york": 0.25,
    "london_ny_overlap": 0.37,  # 37% of daily volume in just 4 hours
}


# ============================================================
# Core Session Detection
# ============================================================


def _get_session_windows(utc_dt: datetime) -> list[SessionWindow]:
    """Calculate active session windows for the given UTC datetime, accounting for DST."""
    windows = []

    # Sydney — affected by Australian DST
    if is_au_dst(utc_dt):
        windows.append(SessionWindow("sydney", 22, 0, 7, 0))  # 22:00-07:00
    else:
        windows.append(SessionWindow("sydney", 21, 0, 6, 0))  # 21:00-06:00

    # Tokyo — no DST
    windows.append(SessionWindow("tokyo", 0, 0, 9, 0))

    # London — affected by UK/EU DST
    if is_eu_dst(utc_dt):
        windows.append(SessionWindow("london", 7, 0, 16, 0))  # 07:00-16:00 BST
    else:
        windows.append(SessionWindow("london", 8, 0, 17, 0))  # 08:00-17:00 GMT

    # New York — affected by US DST
    if is_us_dst(utc_dt):
        windows.append(SessionWindow("new_york", 12, 0, 21, 0))  # 12:00-21:00 EDT
    else:
        windows.append(SessionWindow("new_york", 13, 0, 22, 0))  # 13:00-22:00 EST

    # Calculate overlaps
    _add_overlaps(windows)

    # Mark active sessions
    current_minutes = utc_dt.hour * 60 + utc_dt.minute

    for w in windows:
        if w.start_minutes <= w.end_minutes:
            # Normal: e.g., 08:00-17:00
            w.is_active = w.start_minutes <= current_minutes < w.end_minutes
        else:
            # Wraps midnight: e.g., 21:00-06:00
            w.is_active = (
                current_minutes >= w.start_minutes or current_minutes < w.end_minutes
            )

    return windows


def _add_overlaps(windows: list[SessionWindow]) -> None:
    """Add overlap windows to the list."""
    # Find the base sessions
    sydney = next((w for w in windows if w.name == "sydney"), None)
    tokyo = next((w for w in windows if w.name == "tokyo"), None)
    london = next((w for w in windows if w.name == "london"), None)
    new_york = next((w for w in windows if w.name == "new_york"), None)

    # Sydney-Tokyo overlap
    if sydney and tokyo:
        start = max(sydney.start_minutes, tokyo.start_minutes)
        end = (
            min(sydney.end_minutes, tokyo.end_minutes)
            if sydney.end_minutes > sydney.start_minutes
            else sydney.end_minutes
        )
        if end > start or (
            sydney.end_minutes <= sydney.start_minutes
            and tokyo.end_minutes > sydney.start_minutes
        ):
            # Simplified: just use known overlap
            windows.append(SessionWindow("sydney_tokyo_overlap", 22, 0, 6, 0))

    # Tokyo-London overlap (07:00-09:00 summer, 08:00-09:00 winter)
    if london:
        overlap_start = london.start_hour
        if overlap_start == 7:  # BST
            windows.append(SessionWindow("tokyo_london_overlap", 7, 0, 9, 0))
        else:  # GMT
            windows.append(SessionWindow("tokyo_london_overlap", 8, 0, 9, 0))

    # London-New York overlap (12:00-16:00 summer, 13:00-17:00 winter)
    if new_york and london:
        if new_york.start_hour == 12:  # EDT
            windows.append(SessionWindow("london_ny_overlap", 12, 0, 16, 0))
        else:  # EST
            windows.append(SessionWindow("london_ny_overlap", 13, 0, 17, 0))


def _is_market_open(utc_dt: datetime) -> bool:
    """Forex market is open Sunday 22:00 UTC → Friday 22:00 UTC."""
    weekday = utc_dt.weekday()  # 0=Monday, 6=Sunday
    hour = utc_dt.hour

    # Saturday: closed
    if weekday == 5:
        return False
    # Sunday: opens at 22:00 UTC
    if weekday == 6:
        return hour >= 22
    # Friday: closes at 22:00 UTC
    if weekday == 4:
        return hour < 22
    # Monday-Thursday: always open
    return True


def _get_session_quality_score(session_name: str) -> float:
    """Return a quality score (0-1) for the given session."""
    quality_map = {
        "london_ny_overlap": 1.0,
        "london": 0.85,
        "new_york": 0.8,
        "tokyo_london_overlap": 0.7,
        "tokyo": 0.5,
        "sydney_tokyo_overlap": 0.35,
        "sydney": 0.3,
    }
    return quality_map.get(session_name, 0.0)


def _get_recommended_pairs(
    active_sessions: list[str], active_overlaps: list[str]
) -> list[str]:
    """Return pairs that are optimal for the current session/overlap."""
    all_active = active_sessions + active_overlaps
    recommended = []

    for pair, preferred in PAIR_SESSION_PREFERENCE.items():
        if not preferred:  # 24/7 pairs
            continue
        # Check if any preferred session is active
        if any(s in all_active for s in preferred):
            # Score based on how many preferred sessions are active
            score = sum(1 for s in preferred if s in all_active) / len(preferred)
            if score > 0:
                recommended.append((pair, score))

    # Sort by score descending
    recommended.sort(key=lambda x: x[1], reverse=True)
    return [pair for pair, _ in recommended]


# ============================================================
# Public API
# ============================================================


def get_session_context(utc_dt: Optional[datetime] = None) -> SessionContext:
    """Get complete forex session context for the given UTC time.

    Args:
        utc_dt: UTC datetime. Defaults to now.

    Returns:
        SessionContext with all session data.
    """
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc).replace(tzinfo=None)

    # Ensure naive UTC datetime
    if utc_dt.tzinfo is not None:
        utc_dt = utc_dt.replace(tzinfo=None)

    windows = _get_session_windows(utc_dt)
    active_sessions = [
        w.name for w in windows if w.is_active and "_overlap" not in w.name
    ]
    active_overlaps = [w.name for w in windows if w.is_active and "_overlap" in w.name]

    market_open = _is_market_open(utc_dt)

    # Calculate time to next session
    current_minutes = utc_dt.hour * 60 + utc_dt.minute
    inactive_sessions = [
        w for w in windows if not w.is_active and "_overlap" not in w.name
    ]
    time_to_next = 999
    for w in inactive_sessions:
        if w.start_minutes > current_minutes:
            diff = w.start_minutes - current_minutes
            time_to_next = min(time_to_next, diff)
        elif w.start_minutes < current_minutes:
            # Wraps to next day
            diff = (24 * 60 - current_minutes) + w.start_minutes
            time_to_next = min(time_to_next, diff)

    if time_to_next == 999:
        time_to_next = 0

    # Time to session close (earliest closing active session)
    time_to_close = 999
    for w in windows:
        if w.is_active:
            if w.end_minutes > current_minutes:
                diff = w.end_minutes - current_minutes
                time_to_close = min(time_to_close, diff)
            elif w.end_minutes < current_minutes and w.start_minutes > w.end_minutes:
                # Wraps midnight
                diff = (24 * 60 - current_minutes) + w.end_minutes
                time_to_close = min(time_to_close, diff)

    if time_to_close == 999:
        time_to_close = 0

    # Volume concentration
    volume = 0.0
    for s in active_sessions:
        volume += SESSION_VOLUME.get(s, 0)
    for o in active_overlaps:
        volume = max(
            volume, SESSION_VOLUME.get(o, 0)
        )  # Overlap already includes base sessions

    # Determine overall volatility regime
    if active_overlaps:
        highest_overlap = max(
            active_overlaps, key=lambda o: SESSION_VOLATILITY.get(o, "low")
        )
        volatility_regime = SESSION_VOLATILITY.get(highest_overlap, "normal")
    elif active_sessions:
        highest_session = max(
            active_sessions, key=lambda s: SESSION_VOLATILITY.get(s, "low")
        )
        volatility_regime = SESSION_VOLATILITY.get(highest_session, "normal")
    else:
        volatility_regime = "low"

    # Spread quality
    if active_overlaps:
        best_overlap = min(
            active_overlaps,
            key=lambda o: {"wide": 3, "normal": 2, "tight": 1}.get(
                SESSION_SPREAD_QUALITY.get(o, "normal"), 2
            ),
        )
        spread_quality = SESSION_SPREAD_QUALITY.get(best_overlap, "normal")
    elif active_sessions:
        best_session = min(
            active_sessions,
            key=lambda s: {"wide": 3, "normal": 2, "tight": 1}.get(
                SESSION_SPREAD_QUALITY.get(s, "normal"), 2
            ),
        )
        spread_quality = SESSION_SPREAD_QUALITY.get(best_session, "normal")
    else:
        spread_quality = "wide"

    # Session quality scores for all sessions
    session_quality_scores = {}
    for s in ["sydney", "tokyo", "london", "new_york"] + active_overlaps:
        session_quality_scores[s] = _get_session_quality_score(s)

    # Day of week
    day_name = utc_dt.strftime("%A")
    dow_factor = DAY_OF_WEEK_FACTORS.get(day_name, 1.0)

    # Warnings
    warnings = []
    if not market_open:
        warnings.append("Market is closed (weekend)")
    if utc_dt.weekday() == 4 and utc_dt.hour >= 20:
        warnings.append("Friday late session — liquidity declining, spreads widening")
    if utc_dt.weekday() == 6 and utc_dt.hour < 23:
        warnings.append(
            "Market just opened — expect gaps and wide spreads for first 30 minutes"
        )
    if spread_quality == "wide":
        warnings.append("Wide spreads — consider reducing position size")
    if volatility_regime == "extreme":
        warnings.append("Extreme volatility — use wider stops, expect larger swings")

    # Recommended pairs
    recommended = _get_recommended_pairs(active_sessions, active_overlaps)

    return SessionContext(
        utc_now=utc_dt,
        current_sessions=active_sessions,
        active_overlaps=active_overlaps,
        is_market_open=market_open,
        time_to_next_session=time_to_next,
        time_to_session_close=time_to_close,
        volatility_regime=volatility_regime,
        spread_quality=spread_quality,
        volume_concentration=volume,
        session_quality_scores=session_quality_scores,
        day_of_week=day_name,
        day_of_week_factor=dow_factor,
        warnings=warnings,
        recommended_pairs=recommended,
    )


def get_session_for_pair(symbol: str, utc_dt: Optional[datetime] = None) -> dict:
    """Get session quality score for a specific pair.

    Returns:
        dict with quality_score (0-1), is_optimal, current_session, warnings
    """
    ctx = get_session_context(utc_dt)
    symbol_upper = symbol.upper().replace("/", "")

    preferred = PAIR_SESSION_PREFERENCE.get(symbol_upper, [])

    if not preferred:  # 24/7 pair
        return {
            "symbol": symbol_upper,
            "quality_score": 1.0,
            "is_optimal": True,
            "current_sessions": ctx.current_sessions,
            "active_overlaps": ctx.active_overlaps,
            "is_24_7": True,
            "warnings": [],
        }

    all_active = ctx.current_sessions + ctx.active_overlaps
    optimal_active = [s for s in preferred if s in all_active]

    quality = 0.0
    if optimal_active:
        quality = max(_get_session_quality_score(s) for s in optimal_active)

    # Market closed overrides everything
    if not ctx.is_market_open:
        is_optimal = False
        quality = 0.0
    else:
        is_optimal = bool(optimal_active)

    warnings = []
    if not ctx.is_market_open:
        warnings.append(f"Market closed — {symbol_upper} not tradable")
    elif quality < 0.5:
        warnings.append(
            f"Sub-optimal session for {symbol_upper} — wider spreads, lower liquidity"
        )

    return {
        "symbol": symbol_upper,
        "quality_score": round(quality, 2),
        "is_optimal": is_optimal,
        "current_sessions": ctx.current_sessions,
        "active_overlaps": ctx.active_overlaps,
        "preferred_sessions": preferred,
        "optimal_active": optimal_active,
        "is_24_7": False,
        "warnings": warnings,
    }


def get_day_of_week_factor(utc_dt: Optional[datetime] = None) -> float:
    """Return the day-of-week volatility multiplier."""
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    if utc_dt.tzinfo is not None:
        utc_dt = utc_dt.replace(tzinfo=None)
    return DAY_OF_WEEK_FACTORS.get(utc_dt.strftime("%A"), 1.0)


def is_market_open(utc_dt: Optional[datetime] = None) -> bool:
    """Check if the forex market is currently open."""
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    if utc_dt.tzinfo is not None:
        utc_dt = utc_dt.replace(tzinfo=None)
    return _is_market_open(utc_dt)
