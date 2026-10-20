variable "aws_region" {
  default = "ap-southeast-2"  # Sydney
}

variable "project" {
  default = "iai-legal-demo"
}

variable "openai_api_key" {
  sensitive = true
}

variable "database_url" {
  sensitive = true
}

variable "jwt_secret" {
  sensitive   = true
  default     = "change-me-in-production"
}

variable "admin_password" {
  sensitive = true
  default   = "admin1234"
}

variable "demo_password" {
  sensitive = true
  default   = "demo1234"
}

variable "from_email" {
  default = "noreply@probonoai.com.au"
}

variable "frontend_url" {
  default = "https://www.probonoai.com.au"
}

variable "backend_url" {
  default = "https://api.probonoai.com.au"
}

variable "basic_auth_user" {
  default = "admin"
}

variable "basic_auth_password" {
  sensitive = true
  default   = "admin0000"
}

variable "google_client_id" {
  sensitive = true
  default   = ""
}

variable "google_client_secret" {
  sensitive = true
  default   = ""
}

# Max concurrent executions for the AI Lambda (LLM endpoints). The account's
# unreserved pool must stay >= 100, and it is shared with other projects.
variable "ai_reserved_concurrency" {
  type    = number
  default = 20
}
