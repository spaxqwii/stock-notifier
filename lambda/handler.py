import json
import os
import boto3
from datetime import datetime, timezone
from nse_alert_pipeline import run_alert_pipeline

S3_BUCKET = os.environ["S3_BUCKET"]
s3 = boto3.client("s3", region_name="eu-west-1")


def save_to_s3(payload, key):
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=json.dumps(payload),
        ContentType="application/json"
    )


def save_to_dynamodb(stock, date):
    dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
    table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])
    table.put_item(Item={
        "ticker": stock["ticker"],
        "date": date,
        "name": stock["name"],
        "price": str(stock["price"]),
        "volume": stock["volume"],
        "change": stock["change"],
        "updated_at": datetime.now(timezone.utc).isoformat()
    })


def lambda_handler(event, context):
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        stocks = body["stocks"]
        date = body["date"]

        # 1. Raw dump to S3
        save_to_s3(body, f"raw/{date}.json")

        # 2. Per-stock to DynamoDB
        for stock in stocks:
            save_to_dynamodb(stock, date)

        # 3. Evaluate alerts + Slack notify
        run_alert_pipeline(stocks)

    return {"statusCode": 200, "processed": len(event.get("Records", []))}
