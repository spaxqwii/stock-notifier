#!/bin/bash
set -e

echo "This will destroy ALL resources for the nse-stock-notifier project."
read -p "Type 'destroy' to confirm: " confirm

if [ "$confirm" != "destroy" ]; then
  echo "Aborted. Nothing was changed."
  exit 1
fi

echo "Destroying infrastructure..."
terraform destroy -auto-approve

echo "Done. Double-check the AWS Console (Lambda, DynamoDB, SNS, EventBridge) to confirm nothing remains."
