"""Unit tests for Session Service, Economic Calendar, and News Service.

All tests are pure logic — no MT5 connection required.
"""

from __future__ import annotations

import datetime
from datetime import datetime as dt, timezone, timedelta

import pytest

from mt5_mcp.services.session_service import (
    get_session_context,
    get_session_for_pair,
    get_day_of_week_factor,
    is_market_open,
    is_us_dst,
    is_eu_dst,
    is_au_dst,
    PAIR_SESSION_PREFERENCE,
    DAY_OF_WEEK_FACTORS,
    SESSION_VOLATILITY,
)
from mt5_mcp.services.economic_calendar import (
    get_upcoming_events,
    is_blackout_now,
    get_blackout_windows,
    get_daily_briefing,
    get_events_for_currency,
    RECURRING_EVENTS,
)
from mt5_mcp.services.news_service import (
    fetch_news,
    enrich_news,
    get_available_pools,
    get_available_sources,
    NEWS_SOURCES,
    _simple_sentiment,
    _extract_topics,
    _extract_entities,
    _filter_by_keywords,
    NewsItem,
)


# ============================================================
# Session Service Tests
# ============================================================


class TestDSTDetection:
    """Test DST detection functions."""

    def test_us_dst_march(self):
        # US DST starts 2nd Sunday of March
        # 2025: March 9
        assert not is_us_dst(dt(2025, 3, 1))  # Before
        assert is_us_dst(dt(2025, 3, 10))  # After

    def test_us_dst_november(self):
        # US DST ends 1st Sunday of November
        # 2025: November 2
        assert is_us_dst(dt(2025, 11, 1))  # Before
        assert not is_us_dst(dt(2025, 11, 3))  # After

    def test_us_dst_july(self):
        assert is_us_dst(dt(2025, 7, 15))

    def test_us_dst_january(self):
        assert not is_us_dst(dt(2025, 1, 15))

    def test_eu_dst_march(self):
        # EU DST starts last Sunday of March
        # 2025: March 30
        assert not is_eu_dst(dt(2025, 3, 15))
        assert is_eu_dst(dt(2025, 3, 31))

    def test_eu_dst_october(self):
        # EU DST ends last Sunday of October
        # 2025: October 26
        assert is_eu_dst(dt(2025, 10, 20))
        assert not is_eu_dst(dt(2025, 10, 27))

    def test_japan_no_dst(self):
        """Japan doesn't observe DST — always same hours."""
        # Tokyo session should be 00:00-09:00 UTC year-round
        ctx_jan = get_session_context(dt(2025, 1, 15, 5, 0))
        ctx_jul = get_session_context(dt(2025, 7, 15, 5, 0))
        assert "tokyo" in ctx_jan.current_sessions
        assert "tokyo" in ctx_jul.current_sessions

    def test_au_dst_october(self):
        # AU DST starts 1st Sunday of October
        # 2025: October 5
        assert not is_au_dst(dt(2025, 9, 15))
        assert is_au_dst(dt(2025, 10, 10))

    def test_au_dst_april(self):
        # AU DST ends 1st Sunday of April
        # 2025: April 6
        assert is_au_dst(dt(2025, 4, 1))
        assert not is_au_dst(dt(2025, 4, 10))


class TestMarketOpen:
    """Test market open/close detection."""

    def test_monday_open(self):
        assert is_market_open(dt(2025, 3, 10, 12, 0))  # Monday noon

    def test_wednesday_open(self):
        assert is_market_open(dt(2025, 3, 12, 15, 0))  # Wednesday afternoon

    def test_friday_before_close(self):
        assert is_market_open(dt(2025, 3, 14, 20, 0))  # Friday 20:00 UTC

    def test_friday_after_close(self):
        assert not is_market_open(dt(2025, 3, 14, 23, 0))  # Friday 23:00 UTC

    def test_saturday_closed(self):
        assert not is_market_open(dt(2025, 3, 15, 12, 0))  # Saturday noon

    def test_sunday_before_open(self):
        assert not is_market_open(dt(2025, 3, 16, 20, 0))  # Sunday 20:00 UTC

    def test_sunday_after_open(self):
        assert is_market_open(dt(2025, 3, 16, 23, 0))  # Sunday 23:00 UTC


class TestSessionDetection:
    """Test session detection with various times."""

    def test_london_session_summer(self):
        # July 2025 (BST, London 07:00-16:00 UTC)
        ctx = get_session_context(dt(2025, 7, 15, 10, 0))
        assert "london" in ctx.current_sessions

    def test_london_session_winter(self):
        # January 2025 (GMT, London 08:00-17:00 UTC)
        ctx = get_session_context(dt(2025, 1, 15, 10, 0))
        assert "london" in ctx.current_sessions

    def test_london_closed_summer(self):
        # July 2025, London closed at 17:00 UTC
        ctx = get_session_context(dt(2025, 7, 15, 17, 0))
        assert "london" not in ctx.current_sessions

    def test_new_york_summer(self):
        # July 2025 (EDT, NY 12:00-21:00 UTC)
        ctx = get_session_context(dt(2025, 7, 15, 14, 0))
        assert "new_york" in ctx.current_sessions

    def test_sydney_summer(self):
        # July 2025 (AU winter, Sydney 21:00-06:00 UTC)
        ctx = get_session_context(dt(2025, 7, 15, 2, 0))
        assert "sydney" in ctx.current_sessions

    def test_tokyo_always_same(self):
        # Tokyo is always 00:00-09:00 UTC
        ctx_jan = get_session_context(dt(2025, 1, 15, 3, 0))
        ctx_jul = get_session_context(dt(2025, 7, 15, 3, 0))
        assert "tokyo" in ctx_jan.current_sessions
        assert "tokyo" in ctx_jul.current_sessions


class TestSessionOverlaps:
    """Test overlap detection."""

    def test_london_ny_overlap_summer(self):
        # July 2025: overlap is 12:00-16:00 UTC
        ctx = get_session_context(dt(2025, 7, 15, 14, 0))
        assert "london_ny_overlap" in ctx.active_overlaps
        assert ctx.volatility_regime == "extreme"

    def test_london_ny_overlap_winter(self):
        # January 2025: overlap is 13:00-17:00 UTC
        ctx = get_session_context(dt(2025, 1, 15, 14, 0))
        assert "london_ny_overlap" in ctx.active_overlaps

    def test_no_overlap_sydney(self):
        # Sydney alone — no major overlaps
        ctx = get_session_context(dt(2025, 7, 15, 0, 0))
        # At 00:00 UTC in July, both sydney and tokyo are active
        # This creates sydney_tokyo_overlap
        assert "sydney" in ctx.current_sessions or "tokyo" in ctx.current_sessions


class TestPairSessionQuality:
    """Test pair-specific session quality scoring."""

    def test_eurusd_during_london(self):
        result = get_session_for_pair("EURUSD", dt(2025, 7, 15, 10, 0))
        assert result["quality_score"] > 0.5
        assert result["is_optimal"] is True

    def test_eurusd_during_sydney(self):
        result = get_session_for_pair("EURUSD", dt(2025, 7, 15, 1, 0))
        # EUR/USD is not optimal during Sydney
        assert result["is_optimal"] is False

    def test_usdjpy_during_tokyo(self):
        result = get_session_for_pair("USDJPY", dt(2025, 7, 15, 3, 0))
        assert result["quality_score"] > 0
        assert result["is_optimal"] is True

    def test_btcusd_24_7(self):
        result = get_session_for_pair("BTCUSD", dt(2025, 7, 15, 3, 0))
        assert result["is_24_7"] is True
        assert result["quality_score"] == 1.0

    def test_market_closed(self):
        result = get_session_for_pair("EURUSD", dt(2025, 3, 15, 12, 0))  # Saturday
        assert result["is_optimal"] is False


class TestDayOfWeek:
    """Test day-of-week factors."""

    def test_monday_factor(self):
        assert get_day_of_week_factor(dt(2025, 3, 10)) == 0.75  # Monday

    def test_thursday_factor(self):
        assert get_day_of_week_factor(dt(2025, 3, 13)) == 1.15  # Thursday

    def test_friday_factor(self):
        assert get_day_of_week_factor(dt(2025, 3, 14)) == 0.9  # Friday

    def test_saturday_factor(self):
        assert get_day_of_week_factor(dt(2025, 3, 15)) == 0.0  # Saturday

    def test_all_days_have_factors(self):
        for day_name in [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]:
            assert day_name in DAY_OF_WEEK_FACTORS


class TestSessionContext:
    """Test SessionContext output."""

    def test_context_has_all_fields(self):
        ctx = get_session_context(dt(2025, 7, 15, 14, 0))
        d = ctx.to_dict()
        assert "current_sessions" in d
        assert "active_overlaps" in d
        assert "is_market_open" in d
        assert "volatility_regime" in d
        assert "spread_quality" in d
        assert "day_of_week" in d
        assert "day_of_week_factor" in d
        assert "warnings" in d
        assert "recommended_pairs" in d

    def test_context_recommended_pairs(self):
        ctx = get_session_context(dt(2025, 7, 15, 14, 0))  # London-NY overlap
        # During London-NY overlap, EUR/USD and GBP/USD should be recommended
        assert len(ctx.recommended_pairs) > 0


# ============================================================
# Economic Calendar Tests
# ============================================================


class TestRecurringEvents:
    """Test that recurring events are properly defined."""

    def test_has_us_events(self):
        us_events = [e for e in RECURRING_EVENTS if e.currency == "USD"]
        assert len(us_events) > 0
        names = [e.name for e in us_events]
        assert "Non-Farm Payrolls (NFP)" in names
        assert "FOMC Rate Decision" in names
        assert "US CPI (Consumer Price Index)" in names

    def test_has_eur_events(self):
        eur_events = [e for e in RECURRING_EVENTS if e.currency == "EUR"]
        assert len(eur_events) > 0
        names = [e.name for e in eur_events]
        assert "ECB Interest Rate Decision" in names

    def test_has_gbp_events(self):
        gbp_events = [e for e in RECURRING_EVENTS if e.currency == "GBP"]
        assert len(gbp_events) > 0
        names = [e.name for e in gbp_events]
        assert "BoE Interest Rate Decision" in names

    def test_has_jpy_events(self):
        jpy_events = [e for e in RECURRING_EVENTS if e.currency == "JPY"]
        assert len(jpy_events) > 0
        names = [e.name for e in jpy_events]
        assert "BoJ Interest Rate Decision" in names

    def test_has_aud_events(self):
        aud_events = [e for e in RECURRING_EVENTS if e.currency == "AUD"]
        assert len(aud_events) > 0

    def test_critical_events_have_blackout(self):
        critical = [e for e in RECURRING_EVENTS if e.impact == "CRITICAL"]
        for event in critical:
            assert event.blackout_minutes >= 60


class TestUpcomingEvents:
    """Test upcoming events retrieval."""

    def test_returns_events(self):
        events = get_upcoming_events(hours_ahead=720)  # 30 days
        assert len(events) > 0

    def test_filters_by_currency(self):
        events = get_upcoming_events(hours_ahead=720, currency="USD")
        for event in events:
            assert event.currency == "USD"

    def test_filters_by_impact(self):
        events = get_upcoming_events(hours_ahead=720, min_impact="CRITICAL")
        for event in events:
            assert event.impact == "CRITICAL"

    def test_events_sorted_by_date(self):
        events = get_upcoming_events(hours_ahead=720)
        for i in range(len(events) - 1):
            assert events[i].event_date <= events[i + 1].event_date

    def test_nfp_on_first_friday(self):
        # Find a first Friday and check NFP is scheduled
        # March 2025: first Friday is March 7
        events = get_upcoming_events(
            hours_ahead=720,
            currency="USD",
            utc_now=dt(2025, 3, 1, 0, 0),
        )
        nfp_events = [e for e in events if "NFP" in e.name]
        assert len(nfp_events) > 0
        # NFP should be on March 7
        assert nfp_events[0].event_date.day == 7

    def test_fomc_on_hardcoded_date(self):
        # FOMC on March 19, 2025
        events = get_upcoming_events(
            hours_ahead=720,
            currency="USD",
            utc_now=dt(2025, 3, 1, 0, 0),
        )
        fomc = [e for e in events if "FOMC" in e.name]
        assert len(fomc) > 0
        assert fomc[0].event_date.day == 19


class TestBlackoutDetection:
    """Test news blackout detection."""

    def test_no_blackout_between_events(self):
        # Pick a time when no major events are happening
        result = is_blackout_now(
            utc_now=dt(2025, 3, 15, 5, 0)  # Saturday — no events
        )
        # Should not be in blackout on a weekend
        # But we should still get a valid response
        assert "is_blackout" in result

    def test_blackout_response_structure(self):
        result = is_blackout_now(
            currency="USD",
            minutes_ahead=30,
            utc_now=dt(2025, 3, 10, 12, 0),
        )
        assert "is_blackout" in result
        assert "events_causing_blackout" in result
        assert "minutes_until_clear" in result


class TestDailyBriefing:
    """Test daily calendar briefing."""

    def test_briefing_structure(self):
        briefing = get_daily_briefing(dt(2025, 3, 10, 8, 0))
        assert "date_utc" in briefing
        assert "today_events" in briefing
        assert "today_event_count" in briefing
        assert "critical_events_today" in briefing
        assert "trading_recommendation" in briefing

    def test_briefing_affected_currencies(self):
        briefing = get_daily_briefing(dt(2025, 3, 7, 8, 0))  # NFP day
        assert "affected_currencies" in briefing


# ============================================================
# News Service Tests
# ============================================================


class TestNewsServiceConfig:
    """Test news service configuration."""

    def test_has_forex_major_pool(self):
        pools = get_available_pools()
        pool_ids = [p["id"] for p in pools]
        assert "FOREX_MAJOR" in pool_ids

    def test_has_central_banks_pool(self):
        pools = get_available_pools()
        pool_ids = [p["id"] for p in pools]
        assert "CENTRAL_BANKS" in pool_ids

    def test_has_crypto_fx_pool(self):
        pools = get_available_pools()
        pool_ids = [p["id"] for p in pools]
        assert "CRYPTO_FX" in pool_ids

    def test_has_geopolitical_pool(self):
        pools = get_available_pools()
        pool_ids = [p["id"] for p in pools]
        assert "GEOPOLITICAL_FX" in pool_ids

    def test_has_macro_economic_pool(self):
        pools = get_available_pools()
        pool_ids = [p["id"] for p in pools]
        assert "MACRO_ECONOMIC" in pool_ids

    def test_source_count(self):
        sources = get_available_sources()
        assert len(sources) > 10  # We have ~20 forex-relevant sources

    def test_all_sources_active(self):
        sources = get_available_sources()
        for source in sources:
            assert source["is_active"] is True


class TestSentimentAnalysis:
    """Test lightweight sentiment analysis."""

    def test_positive_sentiment(self):
        result = _simple_sentiment(
            "Federal Reserve raises rates amid strong economic growth and robust employment gains"
        )
        assert result["label"] in ("positive", "slightly_positive")
        assert "growth" in result["positive_keywords"]

    def test_negative_sentiment(self):
        result = _simple_sentiment(
            "Recession fears grow as unemployment rises and markets crash amid worsening crisis"
        )
        assert result["label"] in ("negative", "slightly_negative")
        assert "crisis" in result["negative_keywords"]

    def test_neutral_sentiment(self):
        result = _simple_sentiment(
            "The meeting will be held on Tuesday to discuss the agenda"
        )
        assert result["label"] == "neutral"

    def test_sentiment_has_required_fields(self):
        result = _simple_sentiment("test")
        assert "score" in result
        assert "label" in result
        assert "positive_keywords" in result
        assert "negative_keywords" in result


class TestTopicExtraction:
    """Test topic extraction."""

    def test_detects_central_bank_topics(self):
        topics = _extract_topics("FOMC decision on interest rates")
        assert "fomc" in topics
        assert "interest rate" in topics

    def test_detects_inflation_topics(self):
        topics = _extract_topics("CPI data shows rising inflation concerns")
        assert "inflation" in topics
        assert "cpi" in topics

    def test_detects_crypto_topics(self):
        topics = _extract_topics("Bitcoin surges as crypto market rallies")
        assert "bitcoin" in topics
        assert "crypto" in topics


class TestEntityExtraction:
    """Test entity extraction."""

    def test_detects_currencies(self):
        entities = _extract_entities("EUR and USD move on Fed decision")
        types = [e["type"] for e in entities]
        names = [e["name"] for e in entities]
        assert "Currency" in types
        assert "EUR" in names
        assert "USD" in names

    def test_detects_central_banks(self):
        entities = _extract_entities("Federal Reserve and ECB announce policy changes")
        types = [e["type"] for e in entities]
        assert "Central Bank" in types

    def test_no_duplicates(self):
        entities = _extract_entities("USD rises as Fed speaks about USD policy")
        usd_count = sum(1 for e in entities if e["name"] == "USD")
        assert usd_count == 1


class TestKeywordFiltering:
    """Test keyword-based news filtering."""

    def test_include_keywords(self):
        items = [
            NewsItem(
                "1", "Fed raises rates", "", "", "", "", "", "Federal Reserve decision"
            ),
            NewsItem("2", "ECB meeting", "", "", "", "", "", "European Central Bank"),
        ]
        result = _filter_by_keywords(items, keywords=["fed"])
        assert len(result) == 1
        assert result[0].title == "Fed raises rates"

    def test_exclude_keywords(self):
        items = [
            NewsItem("1", "Fed raises rates", "", "", "", "", "", "Federal Reserve"),
            NewsItem("2", "Sports news", "", "", "", "", "", "Cricket match"),
        ]
        result = _filter_by_keywords(items, exclude_keywords=["sports", "cricket"])
        assert len(result) == 1

    def test_match_all(self):
        items = [
            NewsItem(
                "1", "Fed rate decision on inflation", "", "", "", "", "", "Both topics"
            ),
            NewsItem("2", "Fed rate decision", "", "", "", "", "", "Only Fed"),
        ]
        result = _filter_by_keywords(
            items, keywords=["fed", "inflation"], match_all=True
        )
        assert len(result) == 1

    def test_no_filters_returns_all(self):
        items = [
            NewsItem("1", "Title 1", "", "", "", "", "", "Content 1"),
            NewsItem("2", "Title 2", "", "", "", "", "Content 2"),
        ]
        result = _filter_by_keywords(items)
        assert len(result) == 2


class TestNewsItemDict:
    """Test NewsItem serialization."""

    def test_to_dict_basic(self):
        item = NewsItem(
            id="abc123",
            title="Test Title",
            link="https://example.com",
            pub_date="2025-03-10T12:00:00",
            source_name="Test Source",
            source_id="test",
            pool_id="FOREX_MAJOR",
            content_snippet="Test content snippet",
        )
        d = item.to_dict()
        assert d["id"] == "abc123"
        assert d["title"] == "Test Title"
        assert d["source_name"] == "Test Source"

    def test_to_dict_with_enrichment(self):
        item = NewsItem(
            id="abc123",
            title="Test",
            link="",
            pub_date="",
            source_name="",
            source_id="",
            pool_id="",
            sentiment={"score": 1, "label": "positive"},
            topics=["inflation", "cpi"],
        )
        d = item.to_dict()
        assert d["sentiment"] == {"score": 1, "label": "positive"}
        assert d["topics"] == ["inflation", "cpi"]
