terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  # profile = "jae"  # using default credentials
}

# CloudFront ACM certs must live in us-east-1
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "random_id" "suffix" {
  byte_length = 4
}

# ── Basic auth gate (temporary — remove local + logic below to open access) ────

locals {
  basic_auth_b64 = base64encode("${var.basic_auth_user}:${var.basic_auth_password}")
}

# ── S3 — Lambda deployment artifacts ─────────────────────────────────────────
# Separate from the frontend bucket: Lambda ZIPs shouldn't be mixed with assets
# that CloudFront serves or that GitHub Actions can overwrite on every frontend deploy.

resource "aws_s3_bucket" "lambda_artifacts" {
  bucket = "${var.project}-lambda-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "lambda_artifacts" {
  bucket                  = aws_s3_bucket.lambda_artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "lambda_artifacts" {
  bucket = aws_s3_bucket.lambda_artifacts.id
  versioning_configuration { status = "Enabled" }
}

# ── S3 — case document uploads ────────────────────────────────────────────────

resource "aws_s3_bucket" "uploads" {
  bucket = "${var.project}-uploads-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  cors_rule {
    allowed_headers = ["Content-Type", "Content-Length"]
    allowed_methods = ["PUT"]
    allowed_origins = [
      "https://www.probonoai.com.au",
      "https://preview.probonoai.com.au",
      "http://localhost:20001",
    ]
    max_age_seconds = 3600
  }
}

# ── S3 — frontend static files ────────────────────────────────────────────────

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.project}-frontend-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowCloudFront"
      Effect = "Allow"
      Principal = {
        Service = "cloudfront.amazonaws.com"
      }
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.frontend.arn}/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn
        }
      }
    }]
  })
}

# ── S3 — CloudFront access logs ──────────────────────────────────────────────
# CloudFront log delivery uses legacy ACL grants (awslogsdelivery account), so
# the bucket needs BucketOwnerPreferred ownership and the log-delivery-write
# canned ACL. BlockPublicAcls must be false to allow that ACL to land.

resource "aws_s3_bucket" "cf_logs" {
  bucket        = "${var.project}-cf-logs-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_ownership_controls" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule { object_ownership = "BucketOwnerPreferred" }
}

resource "aws_s3_bucket_acl" "cf_logs" {
  depends_on = [aws_s3_bucket_ownership_controls.cf_logs]
  bucket     = aws_s3_bucket.cf_logs.id
  acl        = "log-delivery-write"
}

resource "aws_s3_bucket_public_access_block" "cf_logs" {
  depends_on              = [aws_s3_bucket_acl.cf_logs]
  bucket                  = aws_s3_bucket.cf_logs.id
  block_public_acls       = false  # log-delivery-write ACL requires this
  block_public_policy     = true
  ignore_public_acls      = false  # allow the delivery ACL to be honoured
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "cf_logs" {
  bucket = aws_s3_bucket.cf_logs.id
  rule {
    id     = "expire-logs"
    status = "Enabled"
    filter {}
    expiration { days = 90 }
  }
}

# ── CloudFront ────────────────────────────────────────────────────────────────

# Rewrite directory-style paths to their index.html before S3 lookup.
# S3 REST API (used with OAC) does not auto-serve index.html for directory
# paths — /search/ would return 403 and fall through to the SPA fallback,
# causing the home page to redirect authenticated users to /chat/.
resource "aws_cloudfront_function" "spa_rewrite" {
  name    = "${var.project}-spa-rewrite"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = <<-EOF
    function handler(event) {
      var request = event.request;
      var uri = request.uri;
      if (uri === '/') return request;
      if (uri.endsWith('/')) {
        request.uri = uri + 'index.html';
        return request;
      }
      var filename = uri.slice(uri.lastIndexOf('/') + 1);
      if (!filename.includes('.')) {
        request.uri = uri + '/index.html';
      }
      return request;
    }
  EOF
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.project}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"  # US/EU/Asia only — cheapest
  aliases             = ["probonoai.com.au", "www.probonoai.com.au", "preview.probonoai.com.au"]

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "s3-frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  default_cache_behavior {
    target_origin_id       = "s3-frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 86400
    max_ttl     = 31536000

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa_rewrite.arn
    }
  }

  # SPA fallback — serve index.html for unknown paths
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
  }

  logging_config {
    bucket          = aws_s3_bucket.cf_logs.bucket_domain_name
    prefix          = "cf/"
    include_cookies = false
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.frontend.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }

  depends_on = [aws_acm_certificate_validation.frontend]
}
