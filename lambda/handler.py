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
import re
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

# Matches lines like "NMG12.70+9.96%" or "EGAD18.50-9.76%" from the
# Top Gainers / Bottom Losers sections of the page.
MOVER_LINE_PATTERN = re.compile(
    r"([A-Z]{2,10})\s*(\d+\.\d{2})\s*([+-]\d+\.\d{2})%"
)

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
    Parse the Top Gainers / Bottom Losers sections into a list of dicts:
    [{ticker, price, change_pct}, ...]

    Why these sections instead of the full listed-companies table: in the
    main table, volume and price digits sit directly adjacent with no
    separator (e.g. "1,261,81085.75" = volume 1,261,810 + price 85.75),
    which is genuinely ambiguous to split reliably in every case. The
    Top Gainers / Bottom Losers sections instead give a clean
    "TICKER + PRICE + ±CHANGE%" grouping per stock (e.g. "NMG12.70+9.96%",
    though depending on the page's table markup, ticker/price/change may
    render as separate cells with whitespace/newlines between them —
    the regex below tolerates either).

    NOTE: only the top ~30 gainers and ~15 losers appear here (not every
    listed stock) — a reasonable trade-off for a threshold-alert use case,
    but it means an obscure stock with a big move could theoretically be
    missed if more than ~30/15 stocks cross the threshold on the same day.

    If the site changes its layout, this function is the only thing that
    should need updating.
    """
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text("\n")

    lower_text = full_text.lower()
    gainers_start = lower_text.find("top gainers")
    losers_start = lower_text.find("bottom losers")
    section_end = lower_text.find("monetary values")
    if section_end == -1:
        section_end = lower_text.find("listed companies")
    if section_end == -1:
        section_end = len(full_text)

    if gainers_start == -1 and losers_start == -1:
        logger.warning("Could not locate Top Gainers / Bottom Losers sections in the page")
        return []

    section_start = gainers_start if gainers_start != -1 else losers_start
    section_text = full_text[section_start:section_end]

    stocks = []
    seen_tickers = set()
    for match in MOVER_LINE_PATTERN.finditer(section_text):
        ticker, price_str, change_str = match.groups()
        if ticker in seen_tickers:
            continue  # avoid double-counting if a ticker appears more than once
        seen_tickers.add(ticker)

        stocks.append({
            "ticker": ticker,
            "price": float(price_str),
            "change_pct": float(change_str),  # the % sign means this IS the percentage already
        })

    return stocks


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
            f"{ticker} is {direction} {abs(change_pct)}% today, "
            f"now trading at KES {price}."
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