# ── Secrets Manager ───────────────────────────────────────────────────────────
#
# All sensitive application secrets live here. Lambda functions receive only
# SECRET_ARN (non-sensitive) and fetch the values at cold start via boto3.
# Sensitive vars are NOT set on Lambda function configurations.
#
# Rotation notes:
#   DATABASE_URL, OPENAI_API_KEY, GOOGLE_CLIENT_SECRET:
#     Rotate manually — update this secret via AWS console or CLI, then
#     trigger a Lambda redeploy or wait for the next cold start.
#
#   JWT_SECRET:
#     Do NOT use auto-rotation without a dual-key (JWKS kid) strategy.
#     Safe manual procedure: update the secret → wait 1 hour (access token
#     TTL) → all tokens signed with the old key will have expired naturally.
#     Refresh tokens are opaque (DB-stored) and are unaffected.
#
#   ADMIN_PASSWORD / DEMO_PASSWORD:
#     Seed user passwords are hashed in the DB at startup. Rotating here
#     requires also re-seeding the DB (call /auth/reset-password or re-run
#     seed_db with the new value).
#
# Terraform state warning:
#   Secret values appear in plaintext in terraform.tfstate. Keep state local
#   (gitignored) or migrate to S3 remote state with SSE + versioning.

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/secrets"
  description             = "Application secrets — fetched by Lambda at cold start via SECRET_ARN"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    DATABASE_URL         = var.database_url
    JWT_SECRET           = var.jwt_secret
    OPENAI_API_KEY       = var.openai_api_key
    GOOGLE_CLIENT_ID     = var.google_client_id
    GOOGLE_CLIENT_SECRET = var.google_client_secret
    ADMIN_PASSWORD       = var.admin_password
    DEMO_PASSWORD        = var.demo_password

    # Previously managed by hand in the console; now in Terraform so `apply`
    # cannot overwrite them. See docs/GPT_ACTION_SETUP.md.
    GPT_OAUTH_CLIENT_ID     = var.gpt_oauth_client_id
    GPT_OAUTH_CLIENT_SECRET = var.gpt_oauth_client_secret
    GPT_ALLOWED_CIDRS       = var.gpt_allowed_cidrs
    MCP_OAUTH_CLIENT_ID     = var.mcp_oauth_client_id
    MCP_OAUTH_CLIENT_SECRET = var.mcp_oauth_client_secret

    # LangSmith tracing. Inputs/outputs are hidden so user questions and case
    # text are not sent to the third party; only structure, latency, tokens, errors.
    LANGSMITH_TRACING              = "true"
    LANGSMITH_ENDPOINT             = "https://apac.api.smith.langchain.com"
    LANGSMITH_API_KEY              = var.langsmith_api_key
    LANGSMITH_PROJECT              = "probono-prod"
    LANGSMITH_HIDE_INPUTS          = "true"
    LANGSMITH_HIDE_OUTPUTS         = "true"
    LANGCHAIN_CALLBACKS_BACKGROUND = "false"
  })
}

output "secret_arn" {
  description = "Set as SECRET_ARN on Lambda functions. Content is fetched at cold start — ARN itself is not sensitive."
  value       = aws_secretsmanager_secret.app.arn
}

# ── CI/CD deploy config ───────────────────────────────────────────────────────
# Non-sensitive deploy targets (bucket names, CloudFront ID) that GitHub Actions
# reads at deploy time via OIDC — instead of duplicating them as GitHub secrets.
# The only value that must stay in GitHub is AWS_DEPLOY_ROLE_ARN, since it's
# needed to authenticate before any AWS/Secrets Manager call can be made.
resource "aws_secretsmanager_secret" "deploy_config" {
  name                    = "${var.project}/deploy-config"
  description             = "CI/CD deploy targets read by GitHub Actions via OIDC"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "deploy_config" {
  secret_id = aws_secretsmanager_secret.deploy_config.id
  secret_string = jsonencode({
    frontend_bucket = aws_s3_bucket.frontend.bucket
    lambda_bucket   = aws_s3_bucket.lambda_artifacts.bucket
    cf_dist_id      = aws_cloudfront_distribution.frontend.id
  })
}
