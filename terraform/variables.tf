variable "project_name" {
  description = "Project prefix for all AWS resources"
  type        = string
  default     = "nse-stock-notifier"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "notification_email" {
  description = "Email for SNS alerts"
  type        = string
}

variable "slack_webhook_url" {
  description = "Slack incoming webhook URL"
  type        = string
  default     = ""
}

variable "threshold_percent" {
  description = "Alert threshold %"
  type        = string
  default     = "5"
}