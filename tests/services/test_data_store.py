"""Tests for DataStore — SQLite-backed historical MT5 data cache."""

import json
import pytest
from mt5_mcp.services.data_store import DataStore


SAMPLE_CSV = """\
<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10
2024.01.01,01:00,1.08550,1.08700,1.08500,1.08650,1200,0,12
2024.01.01,02:00,1.08650,1.08800,1.08600,1.08750,1100,0,11
2024.01.01,03:00,1.08750,1.08900,1.08700,1.08850,1300,0,13
2024.01.01,04:00,1.08850,1.08950,1.08800,1.08900,1400,0,14
"""

SAMPLE_JSON = [
    {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "time": "2024-01-01T00:00:00",
        "open": 1.08500,
        "high": 1.08600,
        "low": 1.08450,
        "close": 1.08550,
        "tick_volume": 1000,
    },
    {
        "symbol": "EURUSD",
        "timeframe": "H1",
        "time": "2024-01-01T01:00:00",
        "open": 1.08550,
        "high": 1.08700,
        "low": 1.08500,
        "close": 1.08650,
        "tick_volume": 1200,
    },
]

SAMPLE_TICKS_CSV = """\
<DATE>,<TIME>,<BID>,<ASK>,<LAST>,<VOLUME>,<FLAGS>
2024.01.01,00:00:00,1.08500,1.08510,1.08505,1.0,1
2024.01.01,00:00:01,1.08501,1.08511,1.08506,2.0,1
"""

SAMPLE_DEALS = [
    {
        "deal_id": 1001,
        "order_id": 5001,
        "position_id": 3001,
        "symbol": "EURUSD",
        "side": "buy",
        "entry": "in",
        "volume": 0.10,
        "price": 1.08500,
        "profit": 0.0,
        "commission": -0.50,
        "swap": 0.0,
        "time": "2024-01-01T00:00:00",
    },
    {
        "deal_id": 1002,
        "order_id": 5002,
        "position_id": 3001,
        "symbol": "EURUSD",
        "side": "sell",
        "entry": "out",
        "volume": 0.10,
        "price": 1.08900,
        "profit": 4.00,
        "commission": -0.50,
        "swap": -0.10,
        "time": "2024-01-01T04:00:00",
    },
]


def _make_store(tmp_path):
    """Create a DataStore backed by a temp file."""
    return DataStore(db_path=str(tmp_path / "test.db"))


class TestInit:
    def test_creates_tables(self, tmp_path):
        store = _make_store(tmp_path)
        stats = store.get_stats()
        assert "bars" in stats
        assert "ticks" in stats
        assert "deals" in stats
        store.close()

    def test_db_at_custom_path(self, tmp_path):
        db_file = tmp_path / "custom.db"
        store = DataStore(db_path=str(db_file))
        assert db_file.exists()
        store.close()


class TestBarsCsvImport:
    def test_imports_csv_with_symbol_timeframe(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")

        assert result["imported"] == 5
        assert result["duplicates_skipped"] == 0
        assert result["errors"] == []
        store.close()

    def test_parses_mt5_datetime_format(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")
        result = store.query_bars("EURUSD", timeframe="H1")

        assert result["data"][0]["time"] == "2024-01-01T00:00:00"
        assert result["data"][1]["time"] == "2024-01-01T01:00:00"
        store.close()

    def test_duplicate_detection(self, tmp_path):
        store = _make_store(tmp_path)
        r1 = store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")
        r2 = store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")

        assert r1["imported"] == 5
        assert r2["imported"] == 0
        assert r2["duplicates_skipped"] == 5
        store.close()

    def test_idempotent_reimport(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")

        result = store.query_bars("EURUSD", timeframe="H1")
        assert result["count"] == 5
        store.close()

    def test_empty_content(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_bars_csv("")
        assert result["imported"] == 0
        assert result["errors"] == []
        store.close()

    def test_missing_symbol_timeframe(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_bars_csv(SAMPLE_CSV)
        assert "symbol and timeframe required" in result["errors"][0]
        store.close()

    def test_corrupt_csv_lines(self, tmp_path):
        store = _make_store(tmp_path)
        corrupt_csv = """\
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10
BADDATE,00:00,1.0,1.0,1.0,1.0,1,0,0
2024.01.01,03:00,1.08750,1.08900,1.08700,1.08850,1300,0,13
"""
        result = store.import_bars_csv(corrupt_csv, symbol="EURUSD", timeframe="H1")

        assert result["imported"] == 2
        assert len(result["errors"]) == 1
        assert "invalid date/time" in result["errors"][0]
        store.close()


class TestBarsJsonImport:
    def test_imports_json(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_bars_json(json.dumps(SAMPLE_JSON))

        assert result["imported"] == 2
        assert result["duplicates_skipped"] == 0
        store.close()

    def test_invalid_json(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_bars_json("not json")
        assert result["imported"] == 0
        assert "Invalid JSON" in result["errors"][0]
        store.close()


class TestBarsQuery:
    def test_date_range_query(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")

        result = store.query_bars(
            "EURUSD",
            timeframe="H1",
            start_time="2024-01-01T01:00:00",
            end_time="2024-01-01T03:00:00",
        )

        assert result["count"] == 3
        assert result["data"][0]["time"] == "2024-01-01T01:00:00"
        assert result["data"][-1]["time"] == "2024-01-01T03:00:00"
        store.close()

    def test_sorted_asc(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")

        result = store.query_bars("EURUSD", timeframe="H1")
        times = [d["time"] for d in result["data"]]
        assert times == sorted(times)
        store.close()

    def test_empty_query(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.query_bars("NONEXISTENT", timeframe="H1")
        assert result["data"] == []
        assert result["count"] == 0
        assert result["source"] == "cache"
        store.close()

    def test_limit(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")

        result = store.query_bars("EURUSD", timeframe="H1", limit=2)
        assert result["count"] == 2
        store.close()

    def test_missing_data_gaps(self, tmp_path):
        store = _make_store(tmp_path)
        csv_with_gaps = """\
2024.01.01,00:00,1.08500,1.08600,1.08450,1.08550,1000,0,10
2024.01.01,05:00,1.08900,1.09000,1.08850,1.08950,1500,0,15
"""
        store.import_bars_csv(csv_with_gaps, symbol="EURUSD", timeframe="H1")
        result = store.query_bars("EURUSD", timeframe="H1")

        assert result["count"] == 2
        assert result["data"][0]["time"] == "2024-01-01T00:00:00"
        assert result["data"][1]["time"] == "2024-01-01T05:00:00"
        store.close()


class TestTicksImport:
    def test_imports_ticks_csv(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_ticks_csv(SAMPLE_TICKS_CSV, symbol="EURUSD")

        assert result["imported"] == 2
        assert result["duplicates_skipped"] == 0
        store.close()

    def test_ticks_empty_content(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_ticks_csv("")
        assert result["imported"] == 0
        store.close()

    def test_query_ticks(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_ticks_csv(SAMPLE_TICKS_CSV, symbol="EURUSD")

        result = store.query_ticks("EURUSD")
        assert result["count"] == 2
        assert result["data"][0]["bid"] == 1.08500
        assert result["data"][1]["bid"] == 1.08501
        store.close()

    def test_query_ticks_time_range(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_ticks_csv(SAMPLE_TICKS_CSV, symbol="EURUSD")

        from datetime import datetime

        t0 = int(datetime(2024, 1, 1, 0, 0, 0).timestamp() * 1000)
        t1 = int(datetime(2024, 1, 1, 0, 0, 1).timestamp() * 1000)

        result = store.query_ticks("EURUSD", start_time_ms=t0, end_time_ms=t1)
        assert result["count"] == 2
        store.close()


class TestDealsImport:
    def test_imports_deals_json(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.import_deals_json(json.dumps(SAMPLE_DEALS))

        assert result["imported"] == 2
        assert result["duplicates_skipped"] == 0
        store.close()

    def test_query_deals(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_deals_json(json.dumps(SAMPLE_DEALS))

        result = store.query_deals()
        assert result["count"] == 2
        assert result["data"][0]["deal_id"] == 1002
        assert result["data"][1]["deal_id"] == 1001
        store.close()

    def test_query_deals_by_symbol(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_deals_json(json.dumps(SAMPLE_DEALS))

        result = store.query_deals(symbol="EURUSD")
        assert result["count"] == 2
        store.close()

    def test_query_deals_empty(self, tmp_path):
        store = _make_store(tmp_path)
        result = store.query_deals()
        assert result["data"] == []
        assert result["count"] == 0
        store.close()


class TestStats:
    def test_stats_empty(self, tmp_path):
        store = _make_store(tmp_path)
        stats = store.get_stats()

        assert stats["bars"]["symbols"] == []
        assert stats["bars"]["total_rows"] == 0
        assert stats["ticks"]["symbols"] == []
        assert stats["ticks"]["total_rows"] == 0
        assert stats["deals"]["total_rows"] == 0
        store.close()

    def test_stats_with_data(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")
        store.import_deals_json(json.dumps(SAMPLE_DEALS))

        stats = store.get_stats()

        assert stats["bars"]["symbols"] == ["EURUSD"]
        assert stats["bars"]["total_rows"] == 5
        assert stats["bars"]["date_range"]["earliest"] == "2024-01-01T00:00:00"
        assert stats["bars"]["date_range"]["latest"] == "2024-01-01T04:00:00"
        assert stats["deals"]["total_rows"] == 2
        store.close()

    def test_stats_multiple_symbols(self, tmp_path):
        store = _make_store(tmp_path)
        store.import_bars_csv(SAMPLE_CSV, symbol="EURUSD", timeframe="H1")
        store.import_bars_csv(SAMPLE_CSV, symbol="XAUUSD", timeframe="H1")

        stats = store.get_stats()
        assert stats["bars"]["symbols"] == ["EURUSD", "XAUUSD"]
        assert stats["bars"]["total_rows"] == 10
        store.close()
