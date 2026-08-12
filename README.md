# stock-notifier

AWS backend for the NSE Kenya stock scraper. Receives daily stock data via SQS from the laptop-side [`nse-scraper`](https://github.com/spaxqwii/nse-scraper), persists it to S3 and DynamoDB, and sends Slack alerts when major stocks move beyond a threshold.

## Architecture

```
┌─────────────────┐     ┌─────────────┐     ┌─────────────────────────────────────┐
│  nse-scraper    │────►│     SQS     │────►│         nse-worker-lambda           │
│  (your laptop)  │     │  (buffer)   │     │  • Save raw JSON → S3               │
│  4:30 PM EAT    │     │             │     │  • Upsert prices → DynamoDB          │
└─────────────────┘     └─────────────┘     │  • Evaluate alerts → Slack (>5%)    │
                                            └─────────────────────────────────────┘
```

| Component | Purpose |
|:---|:---|
| **SQS** | Decouples laptop scraper from Lambda; handles retries if Lambda is throttled |
| **Lambda** | Worker that processes SQS messages, writes to S3/DynamoDB, evaluates alerts |
| **S3** | Raw JSON dumps per day (`s3://bucket/raw/YYYY-MM-DD.json`) |
| **DynamoDB** | Per-ticker, per-day price snapshots for change detection |
| **Slack** | Rich Block Kit alerts when major tickers move ≥5% day-over-day |
| **GitHub Actions** | CI/CD — Terraform plan/apply + Lambda build & deploy |

## Repo Structure

```
stock-notifier/
├── .github/workflows/
│   ├── terraform.yml          # Plan & apply on terraform/ changes
│   └── lambda.yml             # Package & deploy Lambda on lambda/ changes
├── lambda/
│   ├── handler.py             # SQS entrypoint
│   ├── nse_alert_pipeline.py  # Alert logic + Slack formatting
│   └── requirements.txt
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   └── iam.tf
└── README.md
```

## Prerequisites

- AWS account with CLI configured
- Terraform ≥ 1.5
- GitHub repository with OIDC trust to AWS (see IAM setup below)
- Slack webhook URL stored in AWS SSM Parameter Store or GitHub Secrets

## Setup

### 1. Configure AWS OIDC for GitHub Actions

Create an IAM Role that GitHub Actions can assume via OIDC:

```bash
# Trust policy — replace <ACCOUNT_ID> and <REPO>
cat > trust-policy.json << 'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<ACCOUNT_ID>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:spaxqwii/stock-notifier:*"
        }
      }
    }
  ]
}
EOF

aws iam create-role \
  --role-name GitHubActionsTerraformRole \
  --assume-role-policy-document file://trust-policy.json

# Attach policies
aws iam attach-role-policy \
  --role-name GitHubActionsTerraformRole \
  --policy-arn arn:aws:iam::aws:policy/AdministratorAccess

# For Lambda deployment role
aws iam create-role \
  --role-name GitHubActionsLambdaRole \
  --assume-role-policy-document file://trust-policy.json

aws iam put-role-policy \
  --role-name GitHubActionsLambdaRole \
  --policy-name LambdaDeploy \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [
      {"Effect": "Allow", "Action": ["lambda:UpdateFunctionCode"], "Resource": "*"},
      {"Effect": "Allow", "Action": ["s3:PutObject"], "Resource": "arn:aws:s3:::nse-lambda-deployments/*"}
    ]
  }'
```

Add your AWS Account ID to GitHub Secrets:
- Go to **Settings → Secrets and variables → Actions**
- Add `AWS_ACCOUNT_ID`

### 2. Deploy Infrastructure

```bash
cd terraform/
terraform init
terraform apply
```

This creates:
- SQS queue
- Lambda function (`nse-worker-lambda`)
- S3 bucket (`nse-lambda-deployments`)
- DynamoDB table (`nse-daily-snapshots`)
- IAM roles for Lambda execution

### 3. Store Slack Webhook

```bash
aws ssm put-parameter \
  --name "/stock-notifier/slack-webhook-url" \
  --value "https://hooks.slack.com/services/..." \
  --type SecureString \
  --region eu-west-1
```

### 4. Deploy Lambda (first time)

```bash
cd lambda/
pip install -r requirements.txt -t .
zip -r ../lambda.zip .
aws lambda update-function-code \
  --function-name nse-worker-lambda \
  --zip-file fileb://../lambda.zip \
  --region eu-west-1
```

After first deploy, GitHub Actions handles subsequent deployments automatically.

## CI/CD

| Workflow | Trigger | What it does |
|:---|:---|:---|
| `terraform.yml` | Push/PR to `main` changing `terraform/**` | `terraform init` → `plan` (PR) / `apply` (push) |
| `lambda.yml` | Push to `main` changing `lambda/**` | `pip install -t .` → `zip` → upload to S3 → `update-function-code` |

## Major Stock Alert Thresholds

The alert pipeline watches these 15 blue-chip tickers and fires Slack alerts when day-over-day change is ≥ **5%**:

`SCOM`, `EQTY`, `KCB`, `EABL`, `COOP`, `BAT`, `SCBK`, `BAMB`, `KEGN`, `NCBA`, `DTK`, `IMH`, `SBIC`, `JUB`, `ABSA`

## Environment Variables (Lambda)

| Variable | Source | Description |
|:---|:---|:---|
| `SQS_QUEUE_URL` | Terraform output | SQS queue the scraper pushes to |
| `S3_BUCKET` | Terraform output | Raw JSON storage |
| `DYNAMODB_TABLE` | Terraform output | Stock snapshot table |
| `SLACK_WEBHOOK_URL` | SSM Parameter Store | Slack incoming webhook |

## Laptop-Side Scraper

The scraper that feeds this pipeline lives in a **separate repo**:

→ [`spaxqwii/nse-scraper`](https://github.com/spaxqwii/nse-scraper)

It runs on your laptop at 4:30 PM EAT, parses `afx.kwayisi.org/nse/` (handling unclosed HTML5 tags), and pushes parsed stocks to the SQS queue.

**No code coupling** — the only link between repos is the SQS queue URL in the scraper's `.env` file.

## Cost Safety Net

Set up a zero-spend AWS Budget alert:
**Billing → Budgets → Create budget** → $1 threshold with email alert.

## Tearing Down

```bash
cd terraform/
terraform destroy
```

Verify in AWS Console that Lambda, SQS, S3, DynamoDB, and EventBridge rules are removed.

## Notes on the Scraper

The NSE data source (`afx.kwayisi.org/nse/`) serves HTML with **unclosed table tags** (`<td>` without `</td>`). The scraper handles this by splitting rows on `<tr>` and cells on `<td>` directly, then stripping nested tags. If the site changes layout, only the cell extraction regex in `nse-scraper/local_scraper.py` needs updating.
# Wed Aug 12 08:24:11 AM EAT 2026
