variable "aws_region" {
  default = "ap-southeast-2" # Sydney
}

variable "project" {
  default = "iai-legal-demo"
}

# Application secrets (DATABASE_URL, OPENAI_API_KEY, JWT_SECRET, OAuth client
# secrets, LANGSMITH_API_KEY, ...) are deliberately NOT Terraform variables.
# They live only in Secrets Manager and are set outside Terraform; see
# secrets.tf. Do not add them back here: anything passed as a variable ends up
# in plaintext in terraform.tfstate.

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

# Send AI Lambda traces to LangSmith (project probono-prod, inputs/outputs
# hidden). Needs LANGSMITH_API_KEY in the Secrets Manager secret.
variable "langsmith_tracing" {
  type    = bool
  default = true
}

# Where CloudWatch alarms are emailed (see monitoring.tf). Empty = no
# subscription; the alarms still exist and show in the console.
variable "alert_email" {
  type    = string
  default = ""
}

# Max concurrent executions for the AI Lambda (LLM endpoints). The account's
# unreserved pool must stay >= 100, and it is shared with other projects.
variable "ai_reserved_concurrency" {
  type    = number
  default = 20
}
