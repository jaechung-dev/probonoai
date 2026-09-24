# ── GitHub Actions OIDC ───────────────────────────────────────────────────────

data "aws_caller_identity" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # GitHub Actions OIDC intermediate-cert thumbprints. The previous single
  # value was invalid, which made STS reject every AssumeRoleWithWebIdentity.
  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1",
    "1c58a3a8518e8759bf075b76b750d4f2df264fcb",
  ]
}

resource "aws_iam_role" "github_actions" {
  name = "${var.project}-github-actions"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        # GitHub emits immutable numeric IDs in the OIDC sub for this account
        # (repo:owner@<id>/repo@<id>:...). Accept both that form and the plain
        # form so the match doesn't depend on the ID customization staying on.
        StringLike = {
          "token.actions.githubusercontent.com:sub" = [
            "repo:jaechung-dev/probonoai:*",
            "repo:jaechung-dev@210985902/probonoai@1307297934:*",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_actions" {
  name = "deploy"
  role = aws_iam_role.github_actions.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Lambda"
        Effect = "Allow"
        Action = ["lambda:UpdateFunctionCode", "lambda:GetFunction"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project}-api",
          "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project}-ai",
          "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project}-mcp",
          "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:${var.project}-ingest"
        ]
      },
      {
        Sid    = "FrontendS3"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${aws_s3_bucket.frontend.bucket}",
          "arn:aws:s3:::${aws_s3_bucket.frontend.bucket}/*"
        ]
      },
      {
        # GetObject is required: lambda:UpdateFunctionCode with an S3 source
        # validates that the *calling* principal can read the artifact.
        Sid    = "LambdaArtifactsS3"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${aws_s3_bucket.lambda_artifacts.bucket}",
          "arn:aws:s3:::${aws_s3_bucket.lambda_artifacts.bucket}/*"
        ]
      },
      {
        Sid      = "CloudFront"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation"]
        Resource = "arn:aws:cloudfront::${data.aws_caller_identity.current.account_id}:distribution/${aws_cloudfront_distribution.frontend.id}"
      },
      {
        # Read-only, scoped to the deploy-config secret so CI can resolve its
        # deploy targets without them being stored as GitHub secrets.
        Sid      = "DeployConfigSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.deploy_config.arn
      }
    ]
  })
}

output "github_actions_role_arn" {
  value = aws_iam_role.github_actions.arn
}

# ── Shared assume-role trust policy ──────────────────────────────────────────

locals {
  lambda_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# ── API Lambda role ───────────────────────────────────────────────────────────
# Permissions: CloudWatch logs, SES (OTP + password-reset emails),
#              S3 presigned PUT URLs for client document uploads.

resource "aws_iam_role" "lambda_api" {
  name               = "${var.project}-lambda-api"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "lambda_api_logs" {
  role       = aws_iam_role.lambda_api.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_api_ses" {
  name = "ses-send"
  role = aws_iam_role.lambda_api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["ses:SendEmail", "ses:SendRawEmail"]
      # Scoped to the verified domain identity — not * (would allow any identity in account)
      Resource = "arn:aws:ses:${var.aws_region}:${data.aws_caller_identity.current.account_id}:identity/probonoai.com.au"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_api_s3" {
  name = "s3-uploads-presign"
  role = aws_iam_role.lambda_api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject"]
      Resource = "${aws_s3_bucket.uploads.arn}/intakes/*"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_api_secrets" {
  name = "secrets-read"
  role = aws_iam_role.lambda_api.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

# ── AI Lambda role ────────────────────────────────────────────────────────────
# Permissions: CloudWatch logs only.
# DB access is psycopg2 TCP; OpenAI is HTTPS — no AWS service calls at runtime.

resource "aws_iam_role" "lambda_ai" {
  name               = "${var.project}-lambda-ai"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "lambda_ai_logs" {
  role       = aws_iam_role.lambda_ai.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_ai_secrets" {
  name = "secrets-read"
  role = aws_iam_role.lambda_ai.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

# ── MCP Lambda role ───────────────────────────────────────────────────────────
# Permissions: CloudWatch logs only. DB via psycopg2 TCP.

resource "aws_iam_role" "lambda_mcp" {
  name               = "${var.project}-lambda-mcp"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "lambda_mcp_logs" {
  role       = aws_iam_role.lambda_mcp.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_mcp_secrets" {
  name = "secrets-read"
  role = aws_iam_role.lambda_mcp.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

resource "aws_iam_role_policy" "lambda_ingest_secrets" {
  name = "secrets-read"
  role = aws_iam_role.lambda_ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.app.arn
    }]
  })
}

# ── Ingest Lambda role ────────────────────────────────────────────────────────
# Permissions: CloudWatch logs, SQS (consume ingest queue),
#              S3 GetObject (read uploaded PDFs), Textract (OCR).

resource "aws_iam_role" "lambda_ingest" {
  name               = "${var.project}-lambda-ingest"
  assume_role_policy = local.lambda_trust
}

resource "aws_iam_role_policy_attachment" "lambda_ingest_logs" {
  role       = aws_iam_role.lambda_ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_ingest_sqs" {
  name = "sqs-consume"
  role = aws_iam_role.lambda_ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:ChangeMessageVisibility"
      ]
      Resource = [
        aws_sqs_queue.ingest.arn,
        aws_sqs_queue.ingest_dlq.arn
      ]
    }]
  })
}

resource "aws_iam_role_policy" "lambda_ingest_s3" {
  name = "s3-uploads-read"
  role = aws_iam_role.lambda_ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = "${aws_s3_bucket.uploads.arn}/intakes/*"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_ingest_textract" {
  name = "textract"
  role = aws_iam_role.lambda_ingest.id
  policy = jsonencode({
    Version = "2012-10-17"
    # Textract doesn't support resource-level ARNs — * is required here.
    Statement = [{
      Effect   = "Allow"
      Action   = ["textract:DetectDocumentText", "textract:AnalyzeDocument"]
      Resource = "*"
    }]
  })
}
