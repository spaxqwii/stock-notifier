#!/usr/bin/env python3
import os
import json
import boto3
import requests
from datetime import datetime, timezone, timedelta

MAJOR_TICKERS = {"SCOM", "EQTY", "KCB", "EABL", "COOP", "BAT", "SCBK", "BAMB", "KEGN", "NCBA", "DTK", "IMH", "SBIC", "JUB", "ABSA"}
ALERT_THRESHOLD_PCT = 5.0
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "nse-stock-notifier-state")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
table = dynamodb.Table(DYNAMODB_TABLE)

def get_previous_close(ticker):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        item = table.get_item(Key={"ticker": ticker, "date": yesterday}).get("Item")
        return float(item["price"]) if item else None
    except Exception as e:
        print(f"[WARN] Could not fetch previous close for {ticker}: {e}")
        return None

def calculate_pct_change(current, previous):
    return round(((current - previous) / previous) * 100, 2) if previous else 0.0

def format_slack_payload(alerts):
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"🚨 NSE Major Stock Alert — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}", "emoji": True}}, {"type": "divider"}]
    for alert in alerts:
        emoji = "🟢" if alert["change_pct"] > 0 else "🔴"
        blocks.append({"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*{alert['ticker']}* — {alert['name']}"},
            {"type": "mrkdwn", "text": f"{emoji} *{alert['change_pct']:+.2f}%*"},
            {"type": "mrkdwn", "text": f"Price: *KES {alert['price']:,.2f}*"},
            {"type": "mrkdwn", "text": f"Prev: KES {alert['previous_price']:,.2f}"},
            {"type": "mrkdwn", "text": f"Volume: {alert['volume']:,}"},
            {"type": "mrkdwn", "text": f"Threshold: ≥{ALERT_THRESHOLD_PCT}%"}
        ]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_Triggered at {datetime.now(timezone.utc).isoformat()} UTC_"}]})
    return {"blocks": blocks}

def send_slack_alert(payload, webhook_url):
    if not webhook_url or webhook_url == "unset":
        print("[SKIP] No Slack webhook configured")
        print(json.dumps(payload, indent=2))
        return False
    try:
        resp = requests.post(webhook_url, data=json.dumps(payload), headers={"Content-Type": "application/json"}, timeout=10)
        resp.raise_for_status()
        print(f"[OK] Slack alert sent — {resp.status_code}")
        return True
    except Exception as e:
        print(f"[ERR] Slack webhook failed: {e}")
        return False

def evaluate_alerts(stocks):
    alerts = []
    for stock in stocks:
        if stock["ticker"] not in MAJOR_TICKERS:
            continue
        prev = get_previous_close(stock["ticker"])
        if prev is None:
            print(f"[INFO] No baseline for {stock['ticker']}; skipping.")
            continue
        pct = calculate_pct_change(stock["price"], prev)
        if abs(pct) >= ALERT_THRESHOLD_PCT:
            alerts.append({"ticker": stock["ticker"], "name": stock["name"], "price": stock["price"],
                           "previous_price": prev, "change_pct": pct, "volume": stock.get("volume", 0)})
    return alerts

def run_alert_pipeline(stocks, slack_webhook_url=None, dry_run=False):
    alerts = evaluate_alerts(stocks)
    if not alerts:
        print("[OK] No major stocks crossed the ±5% threshold.")
        return []
    print(f"[ALERT] {len(alerts)} major stock(s) triggered:")
    for a in alerts:
        print(f"  {a['ticker']}: {a['change_pct']:+.2f}% (KES {a['previous_price']:.2f} → {a['price']:.2f})")
    payload = format_slack_payload(alerts)
    if dry_run:
        print("\n--- DRY RUN ---")
        print(json.dumps(payload, indent=2))
        return alerts
    send_slack_alert(payload, slack_webhook_url)
    return alerts