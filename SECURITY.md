# Security Policy

Security is treated as a first-class concern in this project — appropriate for a
platform handling legal and personal data.

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub Security Advisories
("Report a vulnerability" on the Security tab) rather than a public issue.
Expected acknowledgement: within 3 business days.

## Automated scanning (CI/CD)

Every push and pull request — and a weekly scheduled run — is scanned by
[`/.github/workflows/security.yml`](.github/workflows/security.yml):

| Layer | Tool | What it catches |
|---|---|---|
| **SAST** | CodeQL (JS/TS + Python) | Injection, unsafe patterns, taint flows |
| **Dependencies** | Dependabot, `npm audit`, `pip-audit` | Known-vulnerable / outdated packages |
| **Containers & IaC** | Trivy (`vuln,secret,misconfig`) | CVEs in images, misconfigurations |
| **Secrets** | Gitleaks | Committed credentials/keys |

Dependabot ([`/.github/dependabot.yml`](.github/dependabot.yml)) opens patch PRs
across npm, pip, Docker base images, and pinned GitHub Actions.

## Application security controls

- **AuthN/AuthZ** — custom JWT, Google OAuth (CSRF state persisted in Postgres),
  and OTP. Passwords are hashed with bcrypt (cost 12, SHA-256 pre-hash);
  accounts created before 2026-10 held salted SHA-256 hashes and are upgraded
  to bcrypt automatically at their next login. Web refresh tokens rotate on
  every use and are chained in a family: replaying an already-rotated token
  revokes the whole family and emails the user.
- **Third-party access (ChatGPT Custom GPT Action)** — OAuth 2.0
  authorization-code + PKCE; separate audience (`probonoai-gpt`); 15-minute
  access tokens; read-only scopes only (`cases:read`, `conversations:read`,
  `search`, `ask`, `timeline`); every route must name its scope (deny by
  default); grants are revocable instantly by the user and on password reset;
  tokens are accepted only from the configured OpenAI IP ranges; per-user rate
  limits with `SECURITY_ALERT` log lines / `security_events` rows; refresh-token
  reuse revokes the grant. Long-lived MCP tokens default to 30 days (max 90).
- **Data ownership (IDOR-safe)** — every case-scoped query is filtered by the
  authenticated `user_id`; a user can only reach their own case data.
- **Least privilege** — per-Lambda IAM roles scoped to only the resources each
  function needs; deploy uses GitHub OIDC (keyless) rather than long-lived keys.
- **Secrets** — runtime configuration is read from AWS Secrets Manager, not the
  repo or environment files.
- **Transport** — HTTPS everywhere (CloudFront + API Gateway).
- **Access logging** — sign-ins are audited to `access_logs`.

## Supported versions

The `main` branch is the supported version; fixes land there first.
