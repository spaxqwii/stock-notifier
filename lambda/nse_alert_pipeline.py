#!/usr/bin/env python3
"""
NSE Major-Stock Alert Module
Fires Slack alerts when blue-chip tickers move >5% day-over-day.
Integrates into: SQS → Worker Lambda → S3 + DynamoDB + SNS/Slack
"""
import os
import json
import boto3
import requests
from decimal import Decimal
from datetime import datetime, timezone

# ── Configuration ───────────────────────────────────────────────
MAJOR_TICKERS = {
    "SCOM", "EQTY", "KCB", "EABL", "COOP", "BAT", "SCBK",
    "BAMB", "KEGN", "NCBA", "DTK", "IMH", "SBIC", "JUB", "ABSA"
}

ALERT_THRESHOLD_PCT = 5.0          # |% change| >= this triggers alert
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "nse-daily-snapshots")

# ── DynamoDB helpers ────────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)


def get_previous_close(ticker: str) -> float | None:
    """
    Fetch yesterday's closing price from DynamoDB.
    Assumes table schema: PK = ticker, SK = YYYY-MM-DD
    """
    yesterday = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        resp = table.get_item(Key={"ticker": ticker, "date": yesterday})
        item = resp.get("Item")
        if item:
            return float(item.get("price", 0))
    except Exception as e:
        print(f"[WARN] Could not fetch previous close for {ticker}: {e}")
    return None


def calculate_pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return round(((current - previous) / previous) * 100, 2)


# ── Slack formatting ────────────────────────────────────────────
def format_slack_payload(alerts: list[dict]) -> dict:
    """
    Build a rich Slack Block Kit message for triggered alerts.
    """
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"🚨 NSE Major Stock Alert — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                "emoji": True
            }
        },
        {"type": "divider"}
    ]

    for alert in alerts:
        emoji = "🟢" if alert["change_pct"] > 0 else "🔴"
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*{alert['ticker']}* — {alert['name']}"},
                {"type": "mrkdwn", "text": f"{emoji} *{alert['change_pct']:+.2f}%*"},
                {"type": "mrkdwn", "text": f"Price: *KES {alert['price']:,.2f}*"},
                {"type": "mrkdwn", "text": f"Prev: KES {alert['previous_price']:,.2f}"},
                {"type": "mrkdwn", "text": f"Volume: {alert['volume']:,}"},
                {"type": "mrkdwn", "text": f"Threshold: ≥{ALERT_THRESHOLD_PCT}%"}
            ]
        })

    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"_Triggered at {datetime.now(timezone.utc).isoformat()} UTC_"}
        ]
    })

    return {"blocks": blocks}


def send_slack_alert(payload: dict) -> bool:
    if not SLACK_WEBHOOK_URL:
        print("[SKIP] No SLACK_WEBHOOK_URL configured — printing payload instead:")
        print(json.dumps(payload, indent=2))
        return False

    try:
        resp = requests.post(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        resp.raise_for_status()
        print(f"[OK] Slack alert sent — {resp.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERR] Slack webhook failed: {e}")
        return False


# ── Main alert engine ───────────────────────────────────────────
def evaluate_alerts(stocks: list[dict]) -> list[dict]:
    """
    Given today's parsed stocks, check which major tickers moved >5%.
    Returns list of alert dicts (empty if none triggered).
    """
    alerts = []
    for stock in stocks:
        ticker = stock["ticker"]
        if ticker not in MAJOR_TICKERS:
            continue

        prev = get_previous_close(ticker)
        if prev is None:
            # First run — no baseline yet; skip alerting but log it
            print(f"[INFO] No baseline for {ticker}; skipping alert check.")
            continue

        pct = calculate_pct_change(stock["price"], prev)
        if abs(pct) >= ALERT_THRESHOLD_PCT:
            alerts.append({
                "ticker": ticker,
                "name": stock["name"],
                "price": stock["price"],
                "previous_price": prev,
                "change_pct": pct,
                "volume": stock.get("volume", 0)
            })

    return alerts


def run_alert_pipeline(stocks: list[dict], dry_run: bool = False) -> list[dict]:
    """
    Full pipeline: evaluate → format → send (or print if dry_run).
    Call this from your Worker Lambda after parsing stocks.
    """
    alerts = evaluate_alerts(stocks)

    if not alerts:
        print("[OK] No major stocks crossed the ±5% threshold today.")
        return []

    print(f"[ALERT] {len(alerts)} major stock(s) triggered the ±{ALERT_THRESHOLD_PCT}% threshold:")
    for a in alerts:
        direction = "UP" if a["change_pct"] > 0 else "DOWN"
        print(f"  {a['ticker']}: {a['change_pct']:+.2f}% {direction} (KES {a['previous_price']:.2f} → {a['price']:.2f})")

    payload = format_slack_payload(alerts)

    if dry_run:
        print("\n--- DRY RUN: Slack payload ---")
        print(json.dumps(payload, indent=2))
        return alerts

    send_slack_alert(payload)
    return alerts


# ── Lambda entrypoint ───────────────────────────────────────────
def lambda_handler(event, context):
    """
    AWS Lambda handler. Expects `event["stocks"]` as list of parsed stock dicts.
    """
    stocks = event.get("stocks", [])
    dry_run = event.get("dry_run", False)
    alerts = run_alert_pipeline(stocks, dry_run=dry_run)
    return {
        "statusCode": 200,
        "alerts_triggered": len(alerts),
        "alerts": alerts
    }


# ── Local test ──────────────────────────────────────────────────
if __name__ == "__main__":
    # Simulated today's data (from your scraper output)
    today_stocks = [
        {"ticker": "SCOM", "name": "Safaricom Plc", "price": 34.55, "volume": 8787890},
        {"ticker": "EQTY", "name": "Equity Group", "price": 85.50, "volume": 484812},
        {"ticker": "JUB",  "name": "Jubilee Holdings", "price": 430.00, "volume": 62},
        {"ticker": "BAT",  "name": "BAT Kenya", "price": 554.00, "volume": 10626},
        {"ticker": "NCBA", "name": "NCBA Group", "price": 92.00, "volume": 14914},
    ]

    # Seed fake "yesterday" prices in DynamoDB (or mock them)
    # For local testing, override get_previous_close to return hardcoded baselines
    _FAKE_BASELINES = {
        "SCOM": 36.20,   # -4.56%  → no alert
        "EQTY": 86.50,   # -1.16%  → no alert
        "JUB":  391.00,  # +9.97%  → ALERT
        "BAT":  560.00,  # -1.07%  → no alert
        "NCBA": 90.00,   # +2.22%  → no alert
    }

    def _mock_prev(ticker):
        return _FAKE_BASELINES.get(ticker)

    get_previous_close = _mock_prev  # swap for local demo

    print("=" * 60)
    print("LOCAL TEST — NSE Major Stock Alert Pipeline")
    print("=" * 60)
    run_alert_pipeline(today_stocks, dry_run=True)
