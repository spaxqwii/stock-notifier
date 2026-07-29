# NSE Kenya Stock Notifier

Scrapes live NSE Kenya stock data every 30 minutes (configurable), and sends
an email/Slack notification when any stock's price moves beyond a threshold
percentage in a single day.

## Architecture
- **Lambda** (Python) — scrapes `afx.kwayisi.org/nse`, parses prices, checks thresholds
- **EventBridge** — triggers the Lambda on a schedule
- **DynamoDB** — tracks the last alerted change per ticker (avoids duplicate alerts)
- **SNS** — sends email notifications
- **SSM Parameter Store** — stores the Slack webhook URL securely
- **CloudWatch** — logs + an alarm if the Lambda errors

All of the above sit in AWS's **Always Free** tier at this project's scale —
no 12-month EC2-style expiry to worry about.

## Setup

1. Install dependencies into the lambda folder (Lambda doesn't ship with `requests`/`bs4`):
   ```bash
   pip install -r lambda/requirements.txt -t lambda/
   ```

2. Copy the example vars file and fill in your details:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```
   Edit `terraform.tfvars` with your email and (optionally) Slack webhook URL.

3. Estimate cost before deploying (optional but recommended):
   ```bash
   infracost breakdown --path .
   ```

4. Deploy:
   ```bash
   terraform init
   terraform apply
   ```

5. **Check your email** and confirm the SNS subscription — notifications won't
   deliver until you click the confirmation link AWS sends you.

## Cost safety net
Before or right after your first `apply`, set up a zero-spend AWS Budget alert:
**Billing → Budgets → Create budget** → set a $1 threshold with an email alert.
This catches you the moment anything would start costing money, independent
of anything in this repo.

## Tearing it down
When you're done for the session:
```bash
./teardown.sh
```
This runs `terraform destroy` after asking you to confirm. Verify in the AWS
Console afterward (Lambda, DynamoDB, SNS, EventBridge, CloudWatch) that
nothing was left behind.

## Notes on the scraper
`lambda/handler.py` parses the NSE table by looking for the table containing
"Ticker" and "Price" headers, then extracts values with regex. If NSE's data
source changes its HTML layout, `parse_stocks()`, `extract_price()`, and
`extract_change_percent()` are the only functions that should need updating.
