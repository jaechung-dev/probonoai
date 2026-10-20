# ── Alerts ────────────────────────────────────────────────────────────────────
# Transactional email (sign-up OTP, password reset) is fire-and-forget:
# services/auth/service.py::_send_email logs "SES_ERROR ..." and still returns
# 200, so a broken SES setup is invisible to users and to the API. This turns
# that log line into an alarm.

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
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
