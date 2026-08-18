#!/bin/bash
# Build Lambda deployment zips
# Usage: ./build_lambda.sh
#
# Produces:
#   api_lambda.zip    — auth, intake, conversations (~10MB, no ML deps)
#   ai_lambda.zip     — search, ask, chat (~50MB, full RAG stack)
#   mcp_lambda.zip    — MCP server (~50MB)
#   ingest_lambda.zip — OCR + chunk + embed (~40MB)

set -e

PY_FLAGS="--platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all:"

# Drop weight that never needs to ship: boto3/botocore are already in the Lambda
# Python runtime, and *.dist-info / __pycache__ are install metadata. This alone
# removes ~20 MB from every package (uvicorn[standard]'s uvloop etc. are handled
# by using plain `uvicorn` in requirements).
slim() {
  local d="$1"
  # boto3/botocore are provided by the Lambda Python runtime
  rm -rf "$d"/boto3 "$d"/botocore
  find "$d" -depth -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  # NOTE: .dist-info dirs are intentionally kept — openai/httpx/mcp read their own
  # metadata via importlib.metadata at import time and crash with ImportModuleError
  # if dist-info is missing. S3-deployed Lambdas have a 250MB unzipped limit so
  # the extra few MB is not a concern.
}

# Remove any stale nested build artifacts before packaging
find services -name "lambda_pkg" -o -name "*_lambda_pkg" | xargs rm -rf 2>/dev/null || true

# ── API Lambda (~10MB) ─────────────────────────────────────────────────────────
echo "→ Building API Lambda (auth, intake, conversations)..."
rm -rf api_lambda_pkg
pip3 install $PY_FLAGS --target api_lambda_pkg -r requirements-api.txt -q

cp -r services/ api_lambda_pkg/services/
# Remove heavy services not needed in the API Lambda
rm -rf api_lambda_pkg/services/ai \
       api_lambda_pkg/services/bff \
       api_lambda_pkg/services/mcp \
       api_lambda_pkg/services/rag \
       api_lambda_pkg/services/ingestion

rm -f api_lambda.zip
slim api_lambda_pkg
cd api_lambda_pkg && zip -r ../api_lambda.zip . -q && cd ..
rm -rf api_lambda_pkg

API_SIZE=$(du -sh api_lambda.zip | cut -f1)
echo "✓ api_lambda.zip ($API_SIZE)"

# ── AI Lambda (~50MB) ──────────────────────────────────────────────────────────
echo "→ Building AI Lambda (search, ask, chat)..."
rm -rf ai_lambda_pkg
pip3 install $PY_FLAGS --target ai_lambda_pkg -r requirements.txt -q

cp -r services/ ai_lambda_pkg/services/
# Remove services not needed in the AI Lambda
rm -rf ai_lambda_pkg/services/api \
       ai_lambda_pkg/services/bff \
       ai_lambda_pkg/services/mcp \
       ai_lambda_pkg/services/ingestion

rm -f ai_lambda.zip
slim ai_lambda_pkg
cd ai_lambda_pkg && zip -r ../ai_lambda.zip . -q && cd ..
rm -rf ai_lambda_pkg

AI_SIZE=$(du -sh ai_lambda.zip | cut -f1)
echo "✓ ai_lambda.zip ($AI_SIZE)"

# ── MCP Lambda (~50MB) ─────────────────────────────────────────────────────────
echo "→ Building MCP Lambda..."
rm -rf mcp_lambda_pkg
pip3 install $PY_FLAGS --target mcp_lambda_pkg -r requirements.txt -q

cp mcp_server.py mcp_lambda_pkg/
cp -r services/ mcp_lambda_pkg/services/
rm -rf mcp_lambda_pkg/services/api \
       mcp_lambda_pkg/services/ai \
       mcp_lambda_pkg/services/bff \
       mcp_lambda_pkg/services/ingestion

rm -f mcp_lambda.zip
slim mcp_lambda_pkg
cd mcp_lambda_pkg && zip -r ../mcp_lambda.zip . -q && cd ..
rm -rf mcp_lambda_pkg

MCP_SIZE=$(du -sh mcp_lambda.zip | cut -f1)
echo "✓ mcp_lambda.zip ($MCP_SIZE)"

# ── Ingest Lambda (~40MB) ──────────────────────────────────────────────────────
echo "→ Building Ingest Lambda (OCR, chunk, embed)..."
rm -rf ingest_lambda_pkg
pip3 install $PY_FLAGS --target ingest_lambda_pkg -r requirements-ingest.txt -q

mkdir -p ingest_lambda_pkg/services/ingestion
cp services/__init__.py ingest_lambda_pkg/services/__init__.py
cp services/ingestion/__init__.py ingest_lambda_pkg/services/ingestion/__init__.py
cp services/ingestion/handler.py  ingest_lambda_pkg/services/ingestion/handler.py
# services/core is needed for _load_secrets() (Secrets Manager loader)
cp -r services/core/ ingest_lambda_pkg/services/core/

rm -f ingest_lambda.zip
slim ingest_lambda_pkg
cd ingest_lambda_pkg && zip -r ../ingest_lambda.zip . -q && cd ..
rm -rf ingest_lambda_pkg

INGEST_SIZE=$(du -sh ingest_lambda.zip | cut -f1)
echo "✓ ingest_lambda.zip ($INGEST_SIZE)"

echo ""
echo "Done. Upload zips to the Lambda artifacts bucket:"
echo "  aws s3 cp api_lambda.zip    s3://\$LAMBDA_BUCKET/api_lambda.zip"
echo "  aws s3 cp ai_lambda.zip     s3://\$LAMBDA_BUCKET/ai_lambda.zip"
echo "  aws s3 cp mcp_lambda.zip    s3://\$LAMBDA_BUCKET/mcp_lambda.zip"
echo "  aws s3 cp ingest_lambda.zip s3://\$LAMBDA_BUCKET/ingest_lambda.zip"
