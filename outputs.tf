output "lambda_function_name" {
  value = aws_lambda_function.scraper.function_name
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.state.name
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "reminder" {
  value = "Check your email and confirm the SNS subscription before alerts will deliver."
}
