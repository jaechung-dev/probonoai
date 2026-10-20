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
# Values are NOT managed by Terraform:
#   Terraform creates the secret (the container) and gives Lambdas its ARN.
#   The JSON values are set and rotated outside Terraform, so they never sit in
#   terraform.tfvars or terraform.tfstate, and `apply` can never overwrite or
#   blank a production secret:
#
#     aws secretsmanager get-secret-value --secret-id iai-legal-demo/secrets   # read
#     aws secretsmanager put-secret-value --secret-id iai-legal-demo/secrets \
#       --secret-string file://secrets.json                                    # replace all keys
#
#   Expected keys: DATABASE_URL JWT_SECRET OPENAI_API_KEY GOOGLE_CLIENT_ID
#   GOOGLE_CLIENT_SECRET ADMIN_PASSWORD DEMO_PASSWORD GPT_OAUTH_CLIENT_ID
#   GPT_OAUTH_CLIENT_SECRET GPT_ALLOWED_CIDRS MCP_OAUTH_CLIENT_ID
#   MCP_OAUTH_CLIENT_SECRET LANGSMITH_API_KEY
#
#   Non-secret settings (e.g. LangSmith project/endpoint) are plain Lambda
#   environment variables in lambda.tf.

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/secrets"
  description             = "Application secrets — fetched by Lambda at cold start via SECRET_ARN"
  recovery_window_in_days = 7
}

# The secret's value used to be an aws_secretsmanager_secret_version fed from
# tfvars. Forget it without touching AWS: the current version (and every value
# in it) stays exactly as it is; it just leaves Terraform state.
removed {
  from = aws_secretsmanager_secret_version.app

  lifecycle {
    destroy = false
  }
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
