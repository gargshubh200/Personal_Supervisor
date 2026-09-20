#!/usr/bin/env bash
# =============================================================================
# deploy_gcp.sh — Deploy Cloud Run Job from local machine
# Compatible: Linux, macOS, Git Bash (Windows), WSL
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — edit these to match your project
# ---------------------------------------------------------------------------
PROJECT_ID="career-os-project"
REGION="asia-south1"
REPO_NAME="career-os-repo"
JOB_NAME="career-os-supervisor-job"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${JOB_NAME}:latest"
ENV_FILE=".env"
# ---------------------------------------------------------------------------

# ── Colour helpers (safe fallback if tput unavailable) ──────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ── Parse .env → temp YAML for --env-vars-file ──────────────────────────────
ENV_YAML=""
parse_env_to_yaml() {
  local env_file="$1"
  # Use relative path in working directory to prevent Git Bash / Windows path translation errors
  ENV_YAML="./.env-temp.yaml"

  info "Parsing ${env_file} → ${ENV_YAML}"
  > "$ENV_YAML"

  while IFS= read -r line || [[ -n "$line" ]]; do
    # Strip Windows-style carriage returns (\r)
    line="${line//$'\r'/}"

    # Strip leading/trailing whitespace
    line="$(echo "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

    # Skip blank lines and comment lines
    [[ -z "$line" || "$line" =~ ^# ]] && continue

    # Strip optional 'export ' prefix
    if [[ "$line" =~ ^export[[:space:]]+ ]]; then
      line="${line#export}"
      line="$(echo "$line" | sed -e 's/^[[:space:]]*//')"
    fi

    # Skip lines without an '=' sign
    [[ "$line" != *"="* ]] && continue

    # Split on first '=' only
    key="${line%%=*}"
    val="${line#*=}"

    # Trim spaces from key and value
    key="$(echo "$key" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    val="$(echo "$val" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

    # Remove inline comments (# comment) if value is unquoted
    if [[ ! "$val" =~ ^\".*\"$ ]] && [[ ! "$val" =~ ^\'.*\'$ ]]; then
      val="${val%%#*}"
      val="$(echo "$val" | sed -e 's/[[:space:]]*$//')"
    fi

    # Strip surrounding single or double quotes
    if [[ "$val" =~ ^\"(.*)\"$ ]]; then
      val="${BASH_REMATCH[1]}"
    elif [[ "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi

    # Escape internal single quotes for YAML safe-quoting
    val_escaped="${val//\'/\'\'}"
    printf "%s: '%s'\n" "$key" "$val_escaped" >> "$ENV_YAML"
  done < "$env_file"

  info "Parsed $(grep -c ':' "$ENV_YAML" || echo 0) env vars"
}

cleanup() {
  [[ -n "$ENV_YAML" && -f "$ENV_YAML" ]] && rm -f "$ENV_YAML"
}
trap cleanup EXIT

# ── Preflight checks ─────────────────────────────────────────────────────────
command -v gcloud &>/dev/null || err "gcloud CLI not found. Install from https://cloud.google.com/sdk/docs/install"

if [[ ! -f "$ENV_FILE" ]]; then
  warn ".env file not found — deploying without environment variables"
else
  parse_env_to_yaml "$ENV_FILE"
fi

# ── Step 1: Set active project ───────────────────────────────────────────────
info "1. Setting active GCP project to ${PROJECT_ID}..."
gcloud config set project "${PROJECT_ID}"

# ── Step 2: Enable APIs ──────────────────────────────────────────────────────
info "2. Enabling required GCP APIs (skipped if already enabled)..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com \
    aiplatform.googleapis.com \
    firestore.googleapis.com \
    drive.googleapis.com

# ── Step 2.5: Grant IAM Permissions to Service Account ─────────────────────
info "2.5. Granting required IAM permissions to Compute Service Account..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

ROLES=(
  "roles/datastore.user"
  "roles/aiplatform.user"
  "roles/secretmanager.secretAccessor"
  "roles/run.invoker"
)

for ROLE in "${ROLES[@]}"; do
  info "   Binding role ${ROLE}..."
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SERVICE_ACCOUNT}" \
      --role="${ROLE}" \
      --quiet >/dev/null
done

# ── Step 3: Ensure Artifact Registry repo exists ─────────────────────────────
info "3. Ensuring Artifact Registry repository exists..."
if ! gcloud artifacts repositories describe "${REPO_NAME}" \
       --location="${REGION}" &>/dev/null; then
  info "   Repository not found — creating..."
  gcloud artifacts repositories create "${REPO_NAME}" \
      --repository-format=docker \
      --location="${REGION}" \
      --description="Docker images for career-os"
else
  info "   Repository already exists — skipping creation."
fi

# ── Step 4: Configure Docker auth for Artifact Registry ──────────────────────
info "4. Configuring Docker authentication..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# ── Step 5: Build & push image via Cloud Build ───────────────────────────────
info "5. Submitting Docker build to Cloud Build..."
gcloud builds submit --tag "${IMAGE_NAME}" .

# ── Step 6: Deploy Cloud Run Job ─────────────────────────────────────────────
info "6. Deploying Cloud Run Job: ${JOB_NAME}..."

DEPLOY_CMD=(
  gcloud run jobs deploy "${JOB_NAME}"
  --image "${IMAGE_NAME}"
  --region "${REGION}"
  --tasks 1
  --max-retries 0
  --task-timeout 2h
)

if [[ -n "$ENV_YAML" && -f "$ENV_YAML" ]]; then
  DEPLOY_CMD+=(--env-vars-file "${ENV_YAML}")
fi

"${DEPLOY_CMD[@]}"

echo ""
info "✅ Deployment completed! Job: ${JOB_NAME} | Region: ${REGION}"
info "   Run it with: gcloud run jobs execute ${JOB_NAME} --region ${REGION}"