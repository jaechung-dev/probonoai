"""
OAuth discovery documents for the ChatGPT MCP connector.

Served by the API Lambda (API Gateway's default route) at the host root:
  /.well-known/oauth-protected-resource[/mcp]   RFC 9728 — describes the MCP server
  /.well-known/oauth-authorization-server       RFC 8414 — describes our /oauth endpoints
  /.well-known/openid-configuration             same document (some clients look here)
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from services.auth.tokens import MCP_ALLOWED_SCOPES, issuer, mcp_audience

router = APIRouter(tags=["oauth-discovery"])

_HEADERS = {"Cache-Control": "public, max-age=300"}


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    return JSONResponse({
        "resource": mcp_audience(),
        "authorization_servers": [issuer()],
        "scopes_supported": MCP_ALLOWED_SCOPES,
        "bearer_methods_supported": ["header"],
        "resource_name": "ProBono AI",
    }, headers=_HEADERS)


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/openid-configuration")
def authorization_server():
    iss = issuer()
    return JSONResponse({
        "issuer": iss,
        "authorization_endpoint": f"{iss}/oauth/authorize",
        "token_endpoint": f"{iss}/oauth/token",
        "revocation_endpoint": f"{iss}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "scopes_supported": MCP_ALLOWED_SCOPES,
        "authorization_response_iss_parameter_supported": True,
    }, headers=_HEADERS)
