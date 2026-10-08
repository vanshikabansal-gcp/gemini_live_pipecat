#!/usr/bin/env bash
# One-command Cloud Run deployment for the Beat Abhay Negotiation Challenge.
# Usage:
#   GCP_PROJECT_ID=your-project-id GEMINI_API_KEY=your-key ./deploy_challenge.sh
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project)}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-abhay-negotiation-challenge}"
ADMIN_PASSWORD="${CHALLENGE_ADMIN_PASSWORD:-lockinout2026}"
MAGIC_WORD="${CHALLENGE_MAGIC_WORD:-zebra,ज़ेबरा,ज़ीब्रा,ज़ेब्रा,ज़ीबरा}"

ENV_VARS="GCP_PROJECT_ID=${PROJECT_ID},GCP_LOCATION=${REGION},GOOGLE_GENAI_USE_VERTEXAI=${GOOGLE_GENAI_USE_VERTEXAI:-FALSE},APP_MODE=abhay-challenge,LEADERBOARD_BACKEND=${LEADERBOARD_BACKEND:-firestore},CHALLENGE_SECONDS=${CHALLENGE_SECONDS:-120},CHALLENGE_REVEAL_TOP_N=${CHALLENGE_REVEAL_TOP_N:-3},CHALLENGE_MAX_CONCURRENT=${CHALLENGE_MAX_CONCURRENT:-25},CHALLENGE_TONE=${CHALLENGE_TONE:-professional},CHALLENGE_ADMIN_PASSWORD=${ADMIN_PASSWORD}"

if [[ -n "${GEMINI_API_KEY:-}" ]]; then
  ENV_VARS="${ENV_VARS},GEMINI_API_KEY=${GEMINI_API_KEY}"
fi

gcloud run deploy "${SERVICE_NAME}" \
  --source=. \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=2 \
  --timeout=900 \
  --concurrency=40 \
  --min-instances=0 \
  --max-instances=6 \
  --set-env-vars="^@^${ENV_VARS}@CHALLENGE_MAGIC_WORD=${MAGIC_WORD}" \
  --quiet
