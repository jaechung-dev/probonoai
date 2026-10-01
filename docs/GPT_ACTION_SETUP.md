# ChatGPT Custom GPT Action — setup & operations

## Settings (AWS Secrets Manager / env)
| Key | Value |
|---|---|
| `GPT_OAUTH_CLIENT_ID` | any random string you choose |
| `GPT_OAUTH_CLIENT_SECRET` | long random secret (also pasted into the GPT's Action OAuth config) |
| `GPT_OAUTH_REDIRECT_URIS` | exact callback URL(s) shown in the GPT's Action settings (comma-separated) |
| `GPT_ALLOWED_CIDRS` | OpenAI's published Action egress ranges (copy from OpenAI's docs; re-check periodically). **Empty = GPT tokens refused.** |
| `TRUSTED_PROXY_HOPS` | see "Verify the caller IP" below |
| `GPT_REQUIRE_PKCE` | `true` (set `false` only if the GPT cannot send `code_challenge`) |
| `MCP_TOKEN_DEFAULT_DAYS` / `MCP_TOKEN_MAX_DAYS` | 30 / 90 |
| `SECURITY_ALERT_EMAIL` | optional: where `[ALERT]` emails go |

## GPT Action OAuth config
- Authorization URL: `https://api.probonoai.com.au/oauth/authorize`
- Token URL: `https://api.probonoai.com.au/oauth/token`
- Scope: `cases:read conversations:read search ask`
- Token exchange method: default (POST). Use the OpenAPI schema with **GET-only** operations.

## Deploy order
1. Run `migrations/2026-10-01_gpt_action_hardening.sql` against Postgres (idempotent).
2. Deploy the API Lambda (new `bcrypt` dependency is in `requirements-api.txt`).
3. Ship the frontend (consent page `/oauth/consent` and the Connected apps panel on `/connect` are built).
4. Set the settings above, then create/connect the GPT.

## Frontend (built: `OAuthConsentPage.tsx`, `ConnectedApps.tsx`)
`/oauth/consent?client_id&redirect_uri&scope&state&code_challenge&code_challenge_method`:
require login, show "ChatGPT wants read-only access to your cases and conversations",
then `POST /oauth/authorize/approve` (Bearer = web session; body = the same params +
`approve: true|false`) and `window.location = response.redirect_to`.
Also a "Connected apps" screen: `GET /oauth/grants`, `DELETE /oauth/grants/{id}`.

## Verify the caller IP
Behind CloudFront + API Gateway the viewer IP position in `X-Forwarded-For` depends on the
chain. Make a request from a known IP, read the `ACCESS ... ip=` log line and adjust
`TRUSTED_PROXY_HOPS` until it shows your own address. A wrong value fails closed (GPT blocked)
or, if set too high, would trust a client-supplied entry — so always confirm with a test.

## Monitoring
Search CloudWatch for `SECURITY_ALERT` (kinds: `refresh_token_reuse`, `oauth_refresh_token_reuse`,
`gpt_token_wrong_ip`, `gpt_scope_denied`, `rate_limit_exceeded`, `oauth_bad_client_auth`,
`gpt_grant_approved`). Add a metric filter + SNS alarm on them. Rows are also in `security_events`.
