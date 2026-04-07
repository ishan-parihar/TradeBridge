#!/usr/bin/env python3
"""Verify all critical MCP server endpoints are responding after deployment."""

import sys
import httpx

BASE_URL = "http://127.0.0.1:8010"

# Critical endpoints that MUST respond (200 or 422 = OK, 404/500 = FAIL)
CRITICAL_ENDPOINTS = [
    (
        "POST",
        "/tools/place_bracket_order",
        {
            "symbol": "XAUUSD",
            "buy_trigger": 2700.0,
            "sell_trigger": 2600.0,
            "volume_lots": 0.01,
        },
        "Bracket orders",
    ),
    (
        "POST",
        "/tools/trading/log_decision",
        {"symbol": "XAUUSD", "side": "buy", "action": "entry"},
        "Trade journal",
    ),
    ("POST", "/tools/wait/delay", {"duration_seconds": 0}, "Wait delay"),
    (
        "POST",
        "/tools/wait/indicator",
        {
            "symbol": "XAUUSD",
            "indicator": "rsi",
            "condition": "above",
            "value": 30,
            "timeout_seconds": 1,
        },
        "Wait indicator",
    ),
    (
        "POST",
        "/resources/market/wait_for_price",
        {
            "symbol": "XAUUSD",
            "condition": "above",
            "price": 2700.0,
            "timeout_seconds": 1,
        },
        "Market wait",
    ),
    ("GET", "/resources/positions/open", None, "Positions open"),
]


def main():
    print(f"Verifying MCP server endpoints at {BASE_URL}")
    print("=" * 70)

    passed = 0
    failed = 0
    results = []

    client = httpx.Client(timeout=10.0)

    for method, endpoint, payload, label in CRITICAL_ENDPOINTS:
        url = f"{BASE_URL}{endpoint}"
        try:
            if method == "POST":
                r = client.post(url, json=payload or {})
            else:
                r = client.get(url)

            # 200, 422 (validation error = endpoint registered) are OK
            # 404, 500 are failures
            ok = r.status_code in (200, 422, 201)
            status = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            else:
                failed += 1

            results.append((label, endpoint, r.status_code, status))
        except httpx.ConnectError:
            failed += 1
            results.append((label, endpoint, "N/A", "FAIL (connection refused)"))
        except Exception as e:
            failed += 1
            results.append((label, endpoint, "N/A", f"FAIL ({e})"))

    # Print summary table
    print(f"{'Endpoint':<50} {'Status':<8} {'Result'}")
    print("-" * 70)
    for label, endpoint, status, result in results:
        result_icon = "✅" if "PASS" in result else "❌"
        print(f"{label + ' ' + endpoint:<50} {str(status):<8} {result_icon} {result}")

    print("-" * 70)
    print(f"Results: {passed} passed, {failed} failed out of {len(CRITICAL_ENDPOINTS)}")

    if failed > 0:
        print("\n⚠️  Some endpoints are not responding. Check server logs.")
        sys.exit(1)
    else:
        print("\n✅ All critical endpoints are responding.")
        sys.exit(0)


if __name__ == "__main__":
    main()
