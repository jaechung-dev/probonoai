# ── Alerts ────────────────────────────────────────────────────────────────────
# Transactional email (sign-up OTP, password reset) is fire-and-forget:
# services/auth/service.py::_send_email logs "SES_ERROR ..." and still returns
# 200, so a broken SES setup is invisible to users and to the API. This turns
# that log line into an alarm.

# Customer-managed key, not alias/aws/sns: CloudWatch alarms cannot publish to
# a topic encrypted with the AWS-managed SNS key, so the key policy must grant
# cloudwatch.amazonaws.com use of it explicitly.
resource "aws_kms_key" "alerts" {
  description         = "${var.project} alerts SNS topic"
  enable_key_rotation = true
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountAdmin"
        Effect    = "Allow"
        Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "AlarmAndSesPublish"
        Effect    = "Allow"
        Principal = { Service = ["cloudwatch.amazonaws.com", "ses.amazonaws.com"] }
        Action    = ["kms:Decrypt", "kms:GenerateDataKey*"]
        Resource  = "*"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      },
    ]
  })
}

resource "aws_kms_alias" "alerts" {
  name          = "alias/${var.project}-alerts"
  target_key_id = aws_kms_key.alerts.key_id
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.project}-alerts"
  kms_master_key_id = aws_kms_key.alerts.arn
}

# Replaces the default topic policy, so it restates the account's own access
# and adds the two AWS services that publish here.
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountOwner"
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action = [
          "SNS:GetTopicAttributes", "SNS:SetTopicAttributes", "SNS:AddPermission",
          "SNS:RemovePermission", "SNS:DeleteTopic", "SNS:Subscribe",
          "SNS:ListSubscriptionsByTopic", "SNS:Publish",
        ]
        Resource  = aws_sns_topic.alerts.arn
        Condition = { StringEquals = { "AWS:SourceOwner" = data.aws_caller_identity.current.account_id } }
      },
      {
        Sid       = "AlarmAndSesPublish"
        Effect    = "Allow"
        Principal = { Service = ["cloudwatch.amazonaws.com", "ses.amazonaws.com"] }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
        Condition = { StringEquals = { "AWS:SourceAccount" = data.aws_caller_identity.current.account_id } }
      },
    ]
  })
}

# ── SES bounce / complaint feedback ───────────────────────────────────────────
# Every bounce and complaint for probonoai.com.au is published to the alerts
# topic (emailed to alert_email). The account-level SES suppression list
# (BOUNCE + COMPLAINT) already stops further sends to those addresses.
resource "aws_ses_identity_notification_topic" "bounce" {
  identity                 = aws_ses_domain_identity.probonoai.domain
  notification_type        = "Bounce"
  topic_arn                = aws_sns_topic.alerts.arn
  include_original_headers = false
  depends_on               = [aws_sns_topic_policy.alerts]
}

resource "aws_ses_identity_notification_topic" "complaint" {
  identity                 = aws_ses_domain_identity.probonoai.domain
  notification_type        = "Complaint"
  topic_arn                = aws_sns_topic.alerts.arn
  include_original_headers = false
  depends_on               = [aws_sns_topic_policy.alerts]
}

# Set alert_email in terraform.tfvars to get notified. AWS sends a confirmation
# email first; alarms are only delivered after the subscription is confirmed.
resource "aws_sns_topic_subscription" "alerts_email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_log_metric_filter" "ses_error" {
  name           = "${var.project}-ses-error"
  log_group_name = aws_cloudwatch_log_group.lambda.name
  pattern        = "\"SES_ERROR\""

  metric_transformation {
    name          = "SesSendErrors"
    namespace     = "${var.project}/email"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "ses_error" {
  alarm_name          = "${var.project}-ses-send-errors"
  alarm_description   = "Transactional email failed to send (SES_ERROR in the API Lambda log). Users are not receiving sign-up codes or password-reset links."
  namespace           = "${var.project}/email"
  metric_name         = "SesSendErrors"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}
