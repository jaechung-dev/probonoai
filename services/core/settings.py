import json
import logging
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _load_secrets() -> None:
    """Fetch Secrets Manager secret and populate os.environ before Pydantic reads it.

    Runs once at cold start when SECRET_ARN is set. No-op in local dev (SECRET_ARN unset).
    Secrets Manager is authoritative when SECRET_ARN is present — values overwrite env.
    """
    arn = os.environ.get("SECRET_ARN")
    if not arn:
        return
    try:
        import boto3
        region = os.environ.get("AWS_REGION_NAME", "ap-southeast-2")
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=arn)
        for key, value in json.loads(resp["SecretString"]).items():
            os.environ[key] = value
    except Exception as exc:
        # Log and continue — Settings validation will raise a clear error
        # if any required var is still missing.
        logger.error("Secrets Manager fetch failed (%s): %s", arn, exc)


_load_secrets()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = ""
    JWT_SECRET: str = ""
    JWT_ALG: str = "HS256"

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    FRONTEND_URL: str = "http://localhost:20001"
    BACKEND_URL: str = "http://localhost:20000"

    APP_NAME: str = "ProBono AI"
    FROM_EMAIL: str = "noreply@probonoai.com.au"
    DEMO_PASSWORD: str = ""
    ADMIN_PASSWORD: str = ""

    EMBED_MODEL: str = "text-embedding-3-small"
    CHAT_MODEL: str = "gpt-4o-mini"

    UPLOADS_BUCKET: str = ""
    AWS_REGION_NAME: str = "ap-southeast-2"

    REDIS_URL: str = ""

    # ── ChatGPT Custom GPT Action (OAuth 2.0 authorization-code + PKCE) ────────
    GPT_OAUTH_CLIENT_ID: str = ""
    GPT_OAUTH_CLIENT_SECRET: str = ""          # keep in Secrets Manager
    # Comma-separated EXACT redirect URIs (copy from the GPT's Action settings).
    GPT_OAUTH_REDIRECT_URIS: str = ""
    GPT_REQUIRE_PKCE: bool = True
    GPT_ACCESS_TOKEN_MINUTES: int = 15
    GPT_REFRESH_TOKEN_DAYS: int = 30
    # Comma-separated CIDRs of OpenAI's published egress ranges. Empty = GPT
    # tokens are REFUSED (fail closed) while GPT_ENFORCE_IP is true.
    GPT_ALLOWED_CIDRS: str = ""
    GPT_ENFORCE_IP: bool = True
    # Number of trusted proxies appended AFTER the real viewer IP in
    # X-Forwarded-For (verify with a test request; wrong value fails closed).
    TRUSTED_PROXY_HOPS: int = 0
    GPT_RATE_PER_MINUTE: int = 30
    GPT_RATE_PER_HOUR: int = 300

    # ── Long-lived MCP tokens ──────────────────────────────────────────────────
    MCP_TOKEN_DEFAULT_DAYS: int = 30
    MCP_TOKEN_MAX_DAYS: int = 90

    # Optional extra recipient for SECURITY_ALERT emails (besides the user).
    SECURITY_ALERT_EMAIL: str = ""


settings = Settings()
