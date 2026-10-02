# Bella Gmail Re-authorisation Runbook

The bella-legal-data export pipeline reads Bella's Gmail via a read-only OAuth
token stored in `token.json` on the iai server. Tokens expire or are revoked
after long idle periods. This runbook covers re-authorising from scratch.

## When to use this

- `invalid_grant: Bad Request` from `export_gmail_label.py`
- `token.json` is missing or corrupt

## Steps

### 1. Remove the stale token

```bash
ssh iai "mv /home/gom/bella-legal-data/token.json /home/gom/bella-legal-data/token.json.bak"
```

### 2. Trigger the auth flow (dry-run is enough)

Run this in the Claude Code prompt using `!` so you can interact with it:

```
! ssh iai "cd /home/gom/bella-legal-data && source .venv/bin/activate && python3 scripts/export_gmail_label.py --label Bella-NCAT --dry-run"
```

The script will print a Google auth URL and then wait.

### 3. Authorise in a browser

1. Open the printed URL in any browser (any device — no tunnel needed).
2. Sign in as the Gmail account that holds the `Bella-*` labels.
3. Grant read-only access.
4. The browser redirects to a `localhost` URL that **fails to load** — that is expected.
5. Copy the full URL from the address bar.

### 4. Paste the redirect URL back

Paste the full `http://localhost/?...code=...` URL into the waiting terminal.
The script exchanges it for a token and writes a fresh `token.json`.

### 5. Run all 8 labels

Once `token.json` is saved, export new emails from all labels with rate-limit
gaps (Gmail allows ~1 full label scan per minute):

```bash
ssh iai "cd /home/gom/bella-legal-data && source .venv/bin/activate && \
  for label in Bella-DOE Bella-GIPA Bella-MacArthur Bella-Medical \
                Bella-NCAT Bella-Others Bella-Police Bella-Politicians; do \
    echo \"=== \$label ===\"; \
    python3 scripts/export_gmail_label.py --label \"\$label\" 2>&1 | tail -5; \
    sleep 65; \
  done"
```

### 6. Extract text from new emails

For any category that gained new emails, run:

```bash
ssh iai "cd /home/gom/bella-legal-data && source .venv/bin/activate && \
  for cat in DOE GIPA MacArthur Medical NCAT Others Police Politicians; do \
    python3 scripts/extract_text.py --category \$cat 2>&1 | tail -3; \
  done"
```

### 7. Upload new .md files to ProbonoAI

Re-run `/tmp/bella_upload.py` (or write a new one scoped to files newer than
the last upload date). The upload script is idempotent via content hashing on
the API side — re-uploading existing content is safe but wastes quota.

## Credentials

| File | Location | Purpose |
|------|----------|---------|
| `credentials.json` | repo root on iai server | Google Cloud OAuth client ID (Desktop app) |
| `token.json` | repo root on iai server | Cached access + refresh token — **never commit** |

`credentials.json` is the Google Cloud OAuth 2.0 client credentials for the
project `201026033248`. If it is lost, recreate it:
Google Cloud Console → APIs & Services → Credentials → Create Credentials →
OAuth client ID → Desktop app → download JSON → save as `credentials.json`.
