"""
NSE Kenya Stock Notifier — Lambda handler

What this does, each time it runs (triggered by EventBridge on a schedule):
  1. Scrapes the public NSE Kenya live data page (afx.kwayisi.org/nse)
  2. Parses ticker, company name, price, and % change for every listed stock
  3. Compares each stock's % change against a threshold
  4. For any stock crossing the threshold, checks DynamoDB to avoid sending
     duplicate alerts for the same move
  5. Sends a notification via SNS (email) and/or a Slack webhook

Environment variables expected (set via Terraform / Lambda config):
  DYNAMODB_TABLE      - name of the DynamoDB table used for state tracking
  SNS_TOPIC_ARN       - ARN of the SNS topic for email notifications
  SLACK_WEBHOOK_PARAM - name of the SSM Parameter Store entry holding the
                         Slack incoming webhook URL (optional, leave blank
                         to skip Slack)
  THRESHOLD_PERCENT   - absolute % change that triggers a notification
                         (e.g. "5" means +/-5%)
"""

import os
import json
import logging
from datetime import datetime, timezone

import boto3
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger()
logger.setLevel(logging.INFO)

NSE_URL = "https://afx.kwayisi.org/nse/"
USER_AGENT = "Mozilla/5.0 (compatible; personal-devops-learning-project/1.0)"

DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "nse-stock-notifier")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
SLACK_WEBHOOK_PARAM = os.environ.get("SLACK_WEBHOOK_PARAM", "")
THRESHOLD_PERCENT = float(os.environ.get("THRESHOLD_PERCENT", "5"))

dynamodb = boto3.resource("dynamodb")
sns = boto3.client("sns")
ssm = boto3.client("ssm")

table = dynamodb.Table(DYNAMODB_TABLE)


def fetch_nse_page():
    """Fetch the raw HTML of the NSE live data page."""
    response = requests.get(NSE_URL, headers={"User-Agent": USER_AGENT}, timeout=10)
    response.raise_for_status()
    return response.text


def parse_stocks(html):
    """
    Parse the listed-companies table into a list of dicts:
    [{ticker, name, volume, price, change_pct}, ...]

    NOTE: this depends on the current HTML structure of the page.
    If the site changes its layout, this function is the only
    thing that should need updating.
    """
    soup = BeautifulSoup(html, "html.parser")
    stocks = []

    # The listed-companies table is the one with Ticker/Name/Volume/Price/Change headers.
    tables = soup.find_all("table")
    target_table = None
    for t in tables:
        header_text = t.get_text(" ", strip=True).lower()
        if "ticker" in header_text and "price" in header_text:
            target_table = t
            break

    if target_table is None:
        logger.warning("Could not locate the stock table in the page HTML")
        return stocks

    rows = target_table.find_all("tr")
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue  # header row or malformed row

        raw_texts = [c.get_text(strip=True) for c in cells]
        # Expected raw layout per row: Ticker, Name, Volume, Price, Change (Change may be split)
        try:
            ticker = raw_texts[0]
            name = raw_texts[1]
            # Volume/price/change formatting varies (some stocks show no trades today)
            rest = " ".join(raw_texts[2:])
            change_pct = extract_change_percent(rest)
            price = extract_price(rest)

            if ticker and price is not None:
                stocks.append({
                    "ticker": ticker,
                    "name": name,
                    "price": price,
                    "change_pct": change_pct,
                })
        except (IndexError, ValueError):
            continue

    return stocks


def extract_price(text):
    """Pull the first plausible decimal number out of a cell's combined text."""
    import re
    match = re.search(r"(\d[\d,]*\.\d{2})", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def extract_change_percent(text):
    """
    The page shows absolute change (e.g. +0.20) next to price, not percentage,
    for the main table. We compute percentage change from price + absolute
    change where possible; otherwise return 0.0.
    """
    import re
    match = re.search(r"([+-]\d+\.\d{2})\s*$", text)
    if not match:
        return 0.0
    change_abs = float(match.group(1))
    price = extract_price(text)
    if not price or price == change_abs:
        return 0.0
    prior_price = price - change_abs
    if prior_price <= 0:
        return 0.0
    return round((change_abs / prior_price) * 100, 2)


def get_last_alerted_change(ticker):
    """Look up the last % change we already alerted on for this ticker today."""
    try:
        response = table.get_item(Key={"ticker": ticker})
        item = response.get("Item")
        return item.get("last_alerted_change") if item else None
    except Exception as exc:
        logger.error(f"DynamoDB read failed for {ticker}: {exc}")
        return None


def save_alert_state(ticker, change_pct, price):
    """Record that we've alerted on this ticker at this change level today."""
    table.put_item(Item={
        "ticker": ticker,
        "last_alerted_change": str(change_pct),
        "last_price": str(price),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def get_slack_webhook_url():
    if not SLACK_WEBHOOK_PARAM:
        return None
    try:
        response = ssm.get_parameter(Name=SLACK_WEBHOOK_PARAM, WithDecryption=True)
        return response["Parameter"]["Value"]
    except Exception as exc:
        logger.warning(f"Could not retrieve Slack webhook from SSM: {exc}")
        return None


def send_slack_message(webhook_url, message):
    try:
        response = requests.post(webhook_url, json={"text": message}, timeout=5)
        response.raise_for_status()
    except Exception as exc:
        logger.error(f"Slack notification failed: {exc}")


def send_email_notification(subject, message):
    if not SNS_TOPIC_ARN:
        logger.info("SNS_TOPIC_ARN not set, skipping email notification")
        return
    try:
        sns.publish(TopicArn=SNS_TOPIC_ARN, Subject=subject, Message=message)
    except Exception as exc:
        logger.error(f"SNS publish failed: {exc}")


def handler(event, context):
    logger.info("Starting NSE stock check")

    html = fetch_nse_page()
    stocks = parse_stocks(html)
    logger.info(f"Parsed {len(stocks)} stocks from NSE page")

    slack_webhook_url = get_slack_webhook_url()
    alerts_sent = []

    for stock in stocks:
        ticker = stock["ticker"]
        change_pct = stock["change_pct"]
        price = stock["price"]

        if abs(change_pct) < THRESHOLD_PERCENT:
            continue

        last_alerted = get_last_alerted_change(ticker)
        if last_alerted == str(change_pct):
            continue  # already alerted on this exact move, avoid duplicate spam

        direction = "up" if change_pct > 0 else "down"
        message = (
            f"{stock['name']} ({ticker}) is {direction} {abs(change_pct)}% "
            f"today, now trading at KES {price}."
        )
        logger.info(f"Threshold crossed: {message}")

        send_email_notification(subject=f"NSE Alert: {ticker} {direction} {abs(change_pct)}%", message=message)
        if slack_webhook_url:
            send_slack_message(slack_webhook_url, message)

        save_alert_state(ticker, change_pct, price)
        alerts_sent.append(message)

    result = {
        "stocks_checked": len(stocks),
        "alerts_sent": len(alerts_sent),
        "messages": alerts_sent,
    }
    logger.info(json.dumps(result))
    return result
