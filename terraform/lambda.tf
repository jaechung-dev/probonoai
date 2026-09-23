# ── API Lambda (auth, intake, conversations, health) ──────────────────────────

resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/${var.project}-api"
  retention_in_days = 7
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.project}-api"
  role             = aws_iam_role.lambda_api.arn
  runtime          = "python3.12"
  handler          = "services.api.main.handler"
  s3_bucket        = aws_s3_bucket.lambda_artifacts.bucket
  s3_key           = "api_lambda.zip"
  timeout          = 30
  memory_size      = 256
  source_code_hash = null

  environment {
    variables = {
      # Sensitive values are fetched from Secrets Manager at cold start via SECRET_ARN.
      SECRET_ARN      = aws_secretsmanager_secret.app.arn
      FROM_EMAIL      = var.from_email
      FRONTEND_URL    = var.frontend_url
      BACKEND_URL     = var.backend_url
      UPLOADS_BUCKET  = aws_s3_bucket.uploads.bucket
      AWS_REGION_NAME = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# ── AI Lambda (search, ask, chat — full RAG stack) ────────────────────────────

resource "aws_cloudwatch_log_group" "ai_lambda" {
  name              = "/aws/lambda/${var.project}-ai"
  retention_in_days = 7
}

resource "aws_lambda_function" "ai" {
  function_name    = "${var.project}-ai"
  role             = aws_iam_role.lambda_ai.arn
  runtime          = "python3.12"
  handler          = "services.ai.main.handler"
  s3_bucket        = aws_s3_bucket.lambda_artifacts.bucket
  s3_key           = "ai_lambda.zip"
  timeout          = 60
  memory_size      = 512
  source_code_hash = null

  environment {
    variables = {
      SECRET_ARN      = aws_secretsmanager_secret.app.arn
      BACKEND_URL     = var.backend_url
      AWS_REGION_NAME = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.ai_lambda]
}

resource "aws_lambda_permission" "apigw_ai" {
  statement_id  = "AllowAPIGatewayInvokeAI"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ai.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

# ── API Gateway ────────────────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "api" {
  name          = var.project
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins     = ["https://www.probonoai.com.au", "https://probonoai.com.au", "http://localhost:5173", "http://localhost:4173", "http://localhost:20001"]
    allow_methods     = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    allow_headers     = ["Content-Type", "Authorization"]
    allow_credentials = true
    max_age           = 86400
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

# API Lambda integration (auth, intake, conversations, health)
resource "aws_apigatewayv2_integration" "lambda" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api.invoke_arn
  payload_format_version = "2.0"
}

# AI Lambda integration (search, ask, chat)
resource "aws_apigatewayv2_integration" "ai" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ai.invoke_arn
  payload_format_version = "2.0"
}

# Catch-all → API Lambda (handles /health, /auth/*, /intake, /user/*, /case/*, /conversations)
resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.lambda.id}"
}

# Explicit AI routes (more specific than $default, so they win)
resource "aws_apigatewayv2_route" "ai_search" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /search"
  target    = "integrations/${aws_apigatewayv2_integration.ai.id}"
}

resource "aws_apigatewayv2_route" "ai_ask" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /ask"
  target    = "integrations/${aws_apigatewayv2_integration.ai.id}"
}

resource "aws_apigatewayv2_route" "ai_chat" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /chat"
  target    = "integrations/${aws_apigatewayv2_integration.ai.id}"
}

# ── MCP Lambda ─────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "mcp_lambda" {
  name              = "/aws/lambda/${var.project}-mcp"
  retention_in_days = 7
}

resource "aws_lambda_function" "mcp" {
  function_name    = "${var.project}-mcp"
  role             = aws_iam_role.lambda_mcp.arn
  runtime          = "python3.12"
  handler          = "mcp_server.handler"
  s3_bucket        = aws_s3_bucket.lambda_artifacts.bucket
  s3_key           = "mcp_lambda.zip"
  timeout          = 60
  memory_size      = 512
  source_code_hash = null

  environment {
    variables = {
      SECRET_ARN      = aws_secretsmanager_secret.app.arn
      RAG_URL         = var.backend_url
      AWS_REGION_NAME = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.mcp_lambda]
}

resource "aws_lambda_permission" "apigw_mcp" {
  statement_id  = "AllowAPIGatewayInvokeMCP"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mcp.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"
}

resource "aws_apigatewayv2_integration" "mcp" {
  api_id                 = aws_apigatewayv2_api.api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.mcp.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "mcp" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /mcp"
  target    = "integrations/${aws_apigatewayv2_integration.mcp.id}"
}

resource "aws_apigatewayv2_route" "mcp_proxy" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "ANY /mcp/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.mcp.id}"
}

# ── Ingestion Lambda (S3 → SQS → Lambda) ─────────────────────────────────────

resource "aws_cloudwatch_log_group" "ingest_lambda" {
  name              = "/aws/lambda/${var.project}-ingest"
  retention_in_days = 14
}

resource "aws_sqs_queue" "ingest_dlq" {
  name                      = "${var.project}-ingest-dlq"
  message_retention_seconds = 1209600  # 14 days
}

resource "aws_sqs_queue" "ingest" {
  name                       = "${var.project}-ingest"
  visibility_timeout_seconds = 720  # > Lambda timeout (600s)
  message_retention_seconds  = 86400
  receive_wait_time_seconds  = 20

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 3
  })
}

# Allow S3 to publish to the SQS queue
resource "aws_sqs_queue_policy" "ingest" {
  queue_url = aws_sqs_queue.ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.ingest.arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = aws_s3_bucket.uploads.arn }
      }
    }]
  })
}

# S3 PutObject on intakes/* → SQS
resource "aws_s3_bucket_notification" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  queue {
    queue_arn     = aws_sqs_queue.ingest.arn
    events        = ["s3:ObjectCreated:Put"]
    filter_prefix = "intakes/"
  }

  depends_on = [aws_sqs_queue_policy.ingest]
}

resource "aws_lambda_function" "ingest" {
  function_name    = "${var.project}-ingest"
  role             = aws_iam_role.lambda_ingest.arn
  runtime          = "python3.12"
  handler          = "services.ingestion.handler.handler"
  s3_bucket        = aws_s3_bucket.lambda_artifacts.bucket
  s3_key           = "ingest_lambda.zip"
  timeout          = 600  # 10 minutes — large PDFs can be slow to OCR
  memory_size      = 1024
  source_code_hash = null

  environment {
    variables = {
      SECRET_ARN      = aws_secretsmanager_secret.app.arn
      AWS_REGION_NAME = var.aws_region
    }
  }

  depends_on = [aws_cloudwatch_log_group.ingest_lambda]
}

# SQS → Ingest Lambda event source mapping
resource "aws_lambda_event_source_mapping" "ingest" {
  event_source_arn                   = aws_sqs_queue.ingest.arn
  function_name                      = aws_lambda_function.ingest.arn
  batch_size                         = 1
  maximum_batching_window_in_seconds = 0
}
