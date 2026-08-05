import boto3, json, os

sqs = boto3.client("sqs")
QUEUE_URL = os.environ["SQS_QUEUE_URL"]
WATCHLIST = os.environ.get("WATCHLIST", "SCOM").split(",")

def handler(event, context):
    for ticker in WATCHLIST:
        ticker = ticker.strip()
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({
                "ticker": ticker,
                "source": "nse-kenya",
                "timestamp": __import__('datetime').datetime.utcnow().isoformat()
            }),
            MessageAttributes={
                "ticker": {"StringValue": ticker, "DataType": "String"}
            }
        )
    return {"queued": len(WATCHLIST)}
