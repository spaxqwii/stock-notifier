#!/bin/bash
set -e

REGION="eu-west-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "=== AWS Account: $ACCOUNT_ID ==="
echo ""

# Check your $0.01 budget status
echo ">>> Budget Status:"
aws budgets describe-budgets --account-id "$ACCOUNT_ID" 2>/dev/null || echo "No budgets found via API (you set yours in console)"

# Check current month forecast via Cost Explorer (only works if Cost Explorer is enabled)
echo ""
echo ">>> Current Month Costs (requires Cost Explorer):"
aws ce get-cost-and-usage \
  --time-period Start=$(date +%Y-%m-01),End=$(date -d "+1 month" +%Y-%m-01) \
  --granularity MONTHLY \
  --metrics BlendedCost \
  --query 'ResultsByTime[0].Total.BlendedCost.[Amount,Unit]' \
  --output table 2>/dev/null || echo "Cost Explorer not enabled. Enable it in AWS Billing Console."

# List running resources by your project tag
echo ""
echo ">>> Resources tagged 'Project=nse-stock-notifier':"
aws resourcegroupstaggingapi get-resources \
  --tag-filters Key=Project,Values=nse-stock-notifier \
  --query 'ResourceTagMappingList[*].[ResourceType,ResourceARN]' \
  --output table 2>/dev/null || echo "Resource Groups Tagging API returned no results."

# Quick service check: Lambda invocations this month
echo ""
echo ">>> Lambda invocations this month:"
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=nse-stock-notifier-scraper \
  --start-time $(date -d "-30 days" +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date +%Y-%m-%dT%H:%M:%SZ) \
  --period 2592000 \
  --statistics Sum \
  --query 'Datapoints[0].Sum' \
  --output text 2>/dev/null || echo "No invocation data yet."

echo ""
echo "=== Done ==="
