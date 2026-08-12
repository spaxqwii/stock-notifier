import json
import os
import boto3
from datetime import datetime, timezone
from nse_alert_pipeline import run_alert_pipeline

S3_BUCKET = os.environ["S3_BUCKET"]
DYNAMODB_TABLE = os.environ["DYNAMODB_TABLE"]
SLACK_WEBHOOK_PARAM = os.environ["SLACK_WEBHOOK_PARAM"]

s3 = boto3.client("s3", region_name="eu-west-1")

def get_slack_webhook():
    ssm = boto3.client("ssm", region_name="eu-west-1")
    resp = ssm.get_parameter(Name=SLACK_WEBHOOK_PARAM, WithDecryption=True)
    return resp["Parameter"]["Value"]

def save_to_s3(payload, key):
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=json.dumps(payload), ContentType="application/json")

def save_to_dynamodb(stock, date):
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table(DYNAMODB_TABLE)
    table.put_item(Item={
        "ticker": stock["ticker"], "date": date, "name": stock["name"],
        "price": str(stock["price"]), "volume": stock["volume"],
        "change": stock["change"], "updated_at": datetime.now(timezone.utc).isoformat()
    })

def lambda_handler(event, context):
    slack_webhook = get_slack_webhook()
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        stocks, date = body["stocks"], body["date"]
        save_to_s3(body, f"raw/{date}.json")
        for stock in stocks:
            save_to_dynamodb(stock, date)
        run_alert_pipeline(stocks, slack_webhook_url=slack_webhook)
    return {"statusCode": 200, "processed": len(event.get("Records", []))}
