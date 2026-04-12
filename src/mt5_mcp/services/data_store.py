"""SQLite-backed cache for historical MT5 data.

Ingests user-exported data (CSV/JSON) and provides date-range queries.
Data directory: ~/.TradeBridge/data/
"""

from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class DataStore:
    """SQLite-backed cache for historical MT5 data."""

    def __init__(self, db_path: str | None = None) -> None:
        """Initialize SQLite connection and create tables if needed.

        Default db_path: ~/.TradeBridge/data/historical.db
        """
        if db_path is None:
            data_dir = Path.home() / ".TradeBridge" / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(data_dir / "historical.db")

        # Ensure parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_tables()

    def _init_tables(self) -> None:
        """Create tables if they don't exist."""
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                tick_volume INTEGER NOT NULL,
                PRIMARY KEY (symbol, timeframe, time)
            );

            CREATE TABLE IF NOT EXISTS ticks (
                symbol TEXT NOT NULL,
                time_msc INTEGER NOT NULL,
                bid REAL NOT NULL,
                ask REAL NOT NULL,
                last REAL,
                volume REAL,
                flags INTEGER,
                PRIMARY KEY (symbol, time_msc)
            );

            CREATE TABLE IF NOT EXISTS deals (
                deal_id INTEGER PRIMARY KEY,
                order_id INTEGER,
                position_id INTEGER,
                symbol TEXT,
                side TEXT,
                entry TEXT,
                volume REAL,
                price REAL,
                profit REAL,
                commission REAL,
                swap REAL,
                time TEXT
            );
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # Import helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_mt5_datetime(date_str: str, time_str: str) -> str | None:
        """Convert MT5 date/time (YYYY.MM.DD, HH:MM) to ISO format.

        Returns ISO format string or None on parse failure.
        """
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y.%m.%d %H:%M")
            return dt.strftime("%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _result(imported: int, duplicates: int, errors: list[str]) -> dict:
        return {
            "imported": imported,
            "duplicates_skipped": duplicates,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Bars import / query
    # ------------------------------------------------------------------

    def import_bars_csv(
        self,
        content: str,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> dict:
        """Import OHLCV bars from MT5-style CSV content.

        Expected CSV format:
        <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>,<VOL>,<SPREAD>
        2024.01.01,00:00,1.0850,1.0860,1.0845,1.0855,1000,0,10
        """
        if not content or not content.strip():
            return self._result(0, 0, [])

        imported = 0
        duplicates = 0
        errors: list[str] = []

        reader = csv.reader(io.StringIO(content.strip()))
        rows_to_insert: list[tuple[str, str, str, float, float, float, float, int]] = []

        for line_num, row in enumerate(reader, start=1):
            # Skip empty rows
            if not row or all(c.strip() == "" for c in row):
                continue

            # Skip header rows (MT5 may or may not have headers)
            if len(row) >= 3:
                first_field = row[0].strip()
                # Detect header: starts with < or equals known header names
                if first_field.startswith("<") or first_field.lower() in (
                    "date",
                    "<date>",
                ):
                    # Try to auto-detect symbol/timeframe from header
                    continue

            if len(row) < 7:
                errors.append(f"Line {line_num}: too few columns ({len(row)})")
                continue

            date_str = row[0].strip()
            time_str = row[1].strip()

            iso_time = self._parse_mt5_datetime(date_str, time_str)
            if iso_time is None:
                errors.append(
                    f"Line {line_num}: invalid date/time '{date_str} {time_str}'"
                )
                continue

            try:
                open_price = float(row[2])
                high = float(row[3])
                low = float(row[4])
                close_price = float(row[5])
                tick_volume = int(row[6])
            except (ValueError, IndexError) as e:
                errors.append(f"Line {line_num}: {e}")
                continue

            if not symbol or not timeframe:
                errors.append(
                    f"Line {line_num}: symbol and timeframe required for CSV import"
                )
                return self._result(imported, duplicates, errors)

            rows_to_insert.append(
                (
                    symbol.upper(),
                    timeframe.upper(),
                    iso_time,
                    open_price,
                    high,
                    low,
                    close_price,
                    tick_volume,
                )
            )

        if rows_to_insert:
            cursor = self._conn.cursor()
            cursor.executemany(
                """INSERT OR IGNORE INTO bars
                   (symbol, timeframe, time, open, high, low, close, tick_volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows_to_insert,
            )
            self._conn.commit()
            imported = cursor.rowcount
            duplicates = len(rows_to_insert) - imported

        return self._result(imported, duplicates, errors)

    def import_bars_json(self, content: str) -> dict:
        """Import bars from JSON array.

        Expected format:
        [{"symbol":"EURUSD","timeframe":"H1","time":"2024-01-01T00:00:00",
          "open":1.0850,"high":1.0860,"low":1.0845,"close":1.0855,
          "tick_volume":1000}, ...]
        """
        if not content or not content.strip():
            return self._result(0, 0, [])

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return self._result(0, 0, [f"Invalid JSON: {e}"])

        if not isinstance(data, list):
            return self._result(0, 0, ["Expected JSON array"])

        imported = 0
        duplicates = 0
        errors: list[str] = []
        rows_to_insert: list[tuple] = []

        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"Item {idx}: not an object")
                continue

            symbol = (item.get("symbol") or "").upper()
            timeframe = (item.get("timeframe") or "").upper()
            time_str = item.get("time", "")

            if not symbol or not timeframe or not time_str:
                errors.append(f"Item {idx}: missing symbol/timeframe/time")
                continue

            try:
                open_price = float(item["open"])
                high = float(item["high"])
                low = float(item["low"])
                close_price = float(item["close"])
                tick_volume = int(item.get("tick_volume", 0))
            except (KeyError, ValueError, TypeError) as e:
                errors.append(f"Item {idx}: {e}")
                continue

            rows_to_insert.append(
                (
                    symbol,
                    timeframe,
                    time_str,
                    open_price,
                    high,
                    low,
                    close_price,
                    tick_volume,
                )
            )

        if rows_to_insert:
            cursor = self._conn.cursor()
            cursor.executemany(
                """INSERT OR IGNORE INTO bars
                   (symbol, timeframe, time, open, high, low, close, tick_volume)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                rows_to_insert,
            )
            self._conn.commit()
            imported = cursor.rowcount
            duplicates = len(rows_to_insert) - imported

        return self._result(imported, duplicates, errors)

    # ------------------------------------------------------------------
    # Ticks import / query
    # ------------------------------------------------------------------

    def import_ticks_csv(self, content: str, symbol: str | None = None) -> dict:
        """Import ticks from CSV.

        Expected format:
        <DATE>,<TIME>,<BID>,<ASK>,<LAST>,<VOLUME>,<FLAGS>
        """
        if not content or not content.strip():
            return self._result(0, 0, [])

        imported = 0
        duplicates = 0
        errors: list[str] = []

        reader = csv.reader(io.StringIO(content.strip()))
        rows_to_insert: list[tuple] = []

        for line_num, row in enumerate(reader, start=1):
            if not row or all(c.strip() == "" for c in row):
                continue

            first_field = row[0].strip()
            if first_field.startswith("<") or first_field.lower() in ("date", "<date>"):
                continue

            if len(row) < 5:
                errors.append(f"Line {line_num}: too few columns ({len(row)})")
                continue

            date_str = row[0].strip()
            time_str = row[1].strip()

            # Parse datetime to milliseconds
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y.%m.%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", "%Y.%m.%d %H:%M")
                except ValueError:
                    errors.append(
                        f"Line {line_num}: invalid date/time '{date_str} {time_str}'"
                    )
                    continue

            time_msc = int(dt.timestamp() * 1000)

            try:
                bid = float(row[2])
                ask = float(row[3])
                last = float(row[4]) if len(row) > 4 and row[4].strip() else None
                volume = float(row[5]) if len(row) > 5 and row[5].strip() else None
                flags = int(row[6]) if len(row) > 6 and row[6].strip() else None
            except (ValueError, IndexError) as e:
                errors.append(f"Line {line_num}: {e}")
                continue

            if not symbol:
                errors.append(f"Line {line_num}: symbol required for CSV import")
                return self._result(imported, duplicates, errors)

            rows_to_insert.append(
                (
                    symbol.upper(),
                    time_msc,
                    bid,
                    ask,
                    last,
                    volume,
                    flags,
                )
            )

        if rows_to_insert:
            cursor = self._conn.cursor()
            cursor.executemany(
                """INSERT OR IGNORE INTO ticks
                   (symbol, time_msc, bid, ask, last, volume, flags)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                rows_to_insert,
            )
            self._conn.commit()
            imported = cursor.rowcount
            duplicates = len(rows_to_insert) - imported

        return self._result(imported, duplicates, errors)

    # ------------------------------------------------------------------
    # Deals import / query
    # ------------------------------------------------------------------

    def import_deals_json(self, content: str) -> dict:
        """Import deals from JSON array."""
        if not content or not content.strip():
            return self._result(0, 0, [])

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return self._result(0, 0, [f"Invalid JSON: {e}"])

        if not isinstance(data, list):
            return self._result(0, 0, ["Expected JSON array"])

        imported = 0
        duplicates = 0
        errors: list[str] = []
        rows_to_insert: list[tuple] = []

        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                errors.append(f"Item {idx}: not an object")
                continue

            try:
                deal_id = int(item["deal_id"])
            except (KeyError, ValueError, TypeError) as e:
                errors.append(f"Item {idx}: missing or invalid deal_id: {e}")
                continue

            order_id = item.get("order_id")
            position_id = item.get("position_id")
            symbol = (item.get("symbol") or "").upper()
            side = item.get("side", "")
            entry = item.get("entry", "")
            volume = float(item["volume"]) if "volume" in item else None
            price = float(item["price"]) if "price" in item else None
            profit = float(item["profit"]) if "profit" in item else None
            commission = float(item["commission"]) if "commission" in item else None
            swap = float(item["swap"]) if "swap" in item else None
            time_str = item.get("time", "")

            rows_to_insert.append(
                (
                    deal_id,
                    order_id,
                    position_id,
                    symbol,
                    side,
                    entry,
                    volume,
                    price,
                    profit,
                    commission,
                    swap,
                    time_str,
                )
            )

        if rows_to_insert:
            cursor = self._conn.cursor()
            cursor.executemany(
                """INSERT OR IGNORE INTO deals
                   (deal_id, order_id, position_id, symbol, side, entry,
                    volume, price, profit, commission, swap, time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows_to_insert,
            )
            self._conn.commit()
            imported = cursor.rowcount
            duplicates = len(rows_to_insert) - imported

        return self._result(imported, duplicates, errors)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_bars(
        self,
        symbol: str,
        timeframe: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 1000,
    ) -> dict:
        """Query bars by symbol + optional date range."""
        sym = symbol.upper()
        conditions: list[str] = ["symbol = ?"]
        params: list[Any] = [sym]

        if timeframe:
            conditions.append("timeframe = ?")
            params.append(timeframe.upper())

        if start_time:
            conditions.append("time >= ?")
            params.append(start_time)

        if end_time:
            conditions.append("time <= ?")
            params.append(end_time)

        where = " AND ".join(conditions)
        query = f"""
            SELECT time, open, high, low, close, tick_volume, symbol, timeframe
            FROM bars WHERE {where}
            ORDER BY time ASC
            LIMIT ?
        """
        params.append(limit)

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        data = []
        actual_timeframe = None
        for row in rows:
            actual_timeframe = row["timeframe"]
            data.append(
                {
                    "time": row["time"],
                    "open": row["open"],
                    "high": row["high"],
                    "low": row["low"],
                    "close": row["close"],
                    "tick_volume": row["tick_volume"],
                }
            )

        return {
            "symbol": symbol,
            "timeframe": timeframe or actual_timeframe or "",
            "data": data,
            "count": len(data),
            "source": "cache",
        }

    def query_ticks(
        self,
        symbol: str,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> dict:
        """Query ticks by symbol + optional time range."""
        sym = symbol.upper()
        conditions: list[str] = ["symbol = ?"]
        params: list[Any] = [sym]

        if start_time_ms is not None:
            conditions.append("time_msc >= ?")
            params.append(start_time_ms)

        if end_time_ms is not None:
            conditions.append("time_msc <= ?")
            params.append(end_time_ms)

        where = " AND ".join(conditions)
        query = f"""
            SELECT symbol, time_msc, bid, ask, last, volume, flags
            FROM ticks WHERE {where}
            ORDER BY time_msc ASC
            LIMIT ?
        """
        params.append(limit)

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        data = []
        for row in rows:
            data.append(
                {
                    "symbol": row["symbol"],
                    "time_msc": row["time_msc"],
                    "bid": row["bid"],
                    "ask": row["ask"],
                    "last": row["last"],
                    "volume": row["volume"],
                    "flags": row["flags"],
                }
            )

        return {
            "symbol": symbol,
            "data": data,
            "count": len(data),
            "source": "cache",
        }

    def query_deals(self, symbol: str | None = None, limit: int = 100) -> dict:
        """Query deals history."""
        conditions: list[str] = []
        params: list[Any] = []

        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol.upper())

        where = " AND ".join(conditions)
        where_clause = f"WHERE {where}" if where else ""

        query = f"""
            SELECT deal_id, order_id, position_id, symbol, side, entry,
                   volume, price, profit, commission, swap, time
            FROM deals {where_clause}
            ORDER BY deal_id DESC
            LIMIT ?
        """
        params.append(limit)

        cursor = self._conn.execute(query, params)
        rows = cursor.fetchall()

        data = []
        for row in rows:
            data.append(
                {
                    "deal_id": row["deal_id"],
                    "order_id": row["order_id"],
                    "position_id": row["position_id"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "entry": row["entry"],
                    "volume": row["volume"],
                    "price": row["price"],
                    "profit": row["profit"],
                    "commission": row["commission"],
                    "swap": row["swap"],
                    "time": row["time"],
                }
            )

        return {
            "data": data,
            "count": len(data),
            "source": "cache",
        }

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return stats about cached data."""
        conn = self._conn

        # Bars stats
        cursor = conn.execute("SELECT COUNT(*), MIN(time), MAX(time) FROM bars")
        bars_row = cursor.fetchone()
        bars_total = bars_row[0] if bars_row else 0
        bars_earliest = bars_row[1] if bars_row else None
        bars_latest = bars_row[2] if bars_row else None

        cursor = conn.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol")
        bars_symbols = [row[0] for row in cursor.fetchall()]

        # Ticks stats
        cursor = conn.execute("SELECT COUNT(*) FROM ticks")
        ticks_total = cursor.fetchone()[0]

        cursor = conn.execute("SELECT DISTINCT symbol FROM ticks ORDER BY symbol")
        ticks_symbols = [row[0] for row in cursor.fetchall()]

        # Deals stats
        cursor = conn.execute("SELECT COUNT(*) FROM deals")
        deals_total = cursor.fetchone()[0]

        return {
            "bars": {
                "symbols": bars_symbols,
                "total_rows": bars_total,
                "date_range": {
                    "earliest": bars_earliest,
                    "latest": bars_latest,
                },
            },
            "ticks": {
                "symbols": ticks_symbols,
                "total_rows": ticks_total,
            },
            "deals": {
                "total_rows": deals_total,
            },
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the SQLite connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "DataStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
