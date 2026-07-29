variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "eu-west-1" # closest AWS region with a full free-tier service set
}

variable "notification_email" {
  description = "Email address to receive SNS alert notifications"
  type        = string
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL (leave blank to skip Slack notifications)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "threshold_percent" {
  description = "Absolute percentage change that triggers a notification"
  type        = string
  default     = "5"
}

variable "schedule_expression" {
  description = "EventBridge schedule expression for how often the scraper runs"
  type        = string
  default     = "rate(30 minutes)"
}

variable "project_name" {
  description = "Used for naming/tagging all resources"
  type        = string
  default     = "nse-stock-notifier"
}
