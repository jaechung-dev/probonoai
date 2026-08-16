output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "api_url" {
  value = "https://api.probonoai.com.au"
}

output "s3_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "lambda_artifacts_bucket" {
  value = aws_s3_bucket.lambda_artifacts.bucket
}

output "cf_logs_bucket" {
  value = aws_s3_bucket.cf_logs.bucket
}
