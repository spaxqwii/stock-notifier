output "sqs_queue_url" {
  value = aws_sqs_queue.scrape_queue.url
}

output "sqs_queue_arn" {
  value = aws_sqs_queue.scrape_queue.arn
}

output "s3_bucket_name" {
  value = aws_s3_bucket.stock_data.id
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.state.name
}

output "lambda_function_name" {
  value = aws_lambda_function.scraper.function_name
}

output "sns_topic_arn" {
  value = aws_sns_topic.alerts.arn
}