terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project = var.project_name
      Owner   = "personal-learning"
      Managed = "terraform"
    }
  }
}

# ---------- DynamoDB: tracks last alerted change per ticker ----------
resource "aws_dynamodb_table" "state" {
  name         = "${var.project_name}-state"
  billing_mode = "PAY_PER_REQUEST" # no capacity to manage, stays within free tier at this scale
  hash_key     = "ticker"

  attribute {
    name = "ticker"
    type = "S"
  }

  deletion_protection_enabled = false # must be false or `terraform destroy` will fail
}

# ---------- SNS: email notifications ----------
resource "aws_sns_topic" "alerts" {
  name = "${var.project_name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.notification_email
  # NOTE: AWS will email you a confirmation link after apply — you must click it
  # once before notifications will actually deliver.
}

# ---------- SSM Parameter Store: Slack webhook (kept out of code/state where possible) ----------
resource "aws_ssm_parameter" "slack_webhook" {
  name  = "/${var.project_name}/slack-webhook-url"
  type  = "SecureString"
  value = var.slack_webhook_url != "" ? var.slack_webhook_url : "unset"
}

# ---------- IAM: least-privilege role for the Lambda ----------
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# ---------- S3: Raw data lake ----------
data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "stock_data" {
  bucket = "${var.project_name}-data-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_lifecycle_configuration" "stock_data" {
  bucket = aws_s3_bucket.stock_data.id
  rule {
    id     = "expire-old-raw"
    status = "Enabled"

    filter {}  # Applies to ALL objects in the bucket

    expiration {
      days = 365  # Keeps you under 5GB forever
    }
  }
}

# ---------- X-Ray ----------
resource "aws_iam_role_policy_attachment" "xray" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.alerts.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = aws_ssm_parameter.slack_webhook.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.stock_data.arn}/raw/*"
      }
    ]
  })
}

# ---------- Lambda packaging ----------
# NOTE: requests + beautifulsoup4 aren't in the standard Lambda runtime, so they
# need to be packaged into the deployment zip (or a Lambda layer). This assumes
# you've run `pip install -r lambda/requirements.txt -t lambda/` before `terraform apply`,
# so those libraries sit alongside handler.py in the zipped folder.
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/lambda"
  output_path = "${path.module}/build/lambda.zip"
}

resource "aws_lambda_function" "scraper" {
  function_name    = "${var.project_name}-scraper"
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

   tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      DYNAMODB_TABLE       = aws_dynamodb_table.state.name
      SNS_TOPIC_ARN        = aws_sns_topic.alerts.arn
      SLACK_WEBHOOK_PARAM  = aws_ssm_parameter.slack_webhook.name
      THRESHOLD_PERCENT    = var.threshold_percent
      DATA_BUCKET = aws_s3_bucket.stock_data.id
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.scraper.function_name}"
  retention_in_days = 7 # keep log storage small and within free tier
}


# ---------- CloudWatch Alarm: notify if the Lambda errors ----------
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.project_name}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 3600
  statistic           = "Sum"
  threshold           = 0
  alarm_actions       = [aws_sns_topic.alerts.arn]

  dimensions = {
    FunctionName = aws_lambda_function.scraper.function_name
  }
}

# ---------- SQS ----------
resource "aws_sqs_queue" "scrape_queue" {
  name                       = "${var.project_name}-scrape-queue"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 86400
}

resource "aws_sqs_queue" "dlq" {
  name = "${var.project_name}-scrape-dlq"
}

resource "aws_sqs_queue_redrive_policy" "scrape_queue" {
  queue_url = aws_sqs_queue.scrape_queue.id
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}


# ---------- Worker: SQS trigger ----------
resource "aws_lambda_event_source_mapping" "worker_sqs" {
  event_source_arn = aws_sqs_queue.scrape_queue.arn
  function_name    = aws_lambda_function.scraper.arn
  batch_size       = 1
}

# Add SQS permissions to existing worker role
resource "aws_iam_role_policy" "lambda_sqs" {
  name = "${var.project_name}-lambda-sqs"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      Resource = aws_sqs_queue.scrape_queue.arn
    }]
  })
}

