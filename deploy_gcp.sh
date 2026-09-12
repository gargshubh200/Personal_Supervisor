#!/bin/bash
set -e

# Load local .env variables into environment if present
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Configuration
PROJECT_ID="career-os-project"
REGION="asia-south1"
REPO_NAME="career-os-repo"
JOB_NAME="career-os-supervisor-job"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${JOB_NAME}:latest"

echo "1. Setting active project to ${PROJECT_ID}..."
gcloud config set project ${PROJECT_ID}

echo "2. Enabling Required GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com \
    aiplatform.googleapis.com \
    firestore.googleapis.com

echo "3. Submitting Docker build to Cloud Build..."
gcloud builds submit --tag ${IMAGE_NAME} .

echo "4. Deploying Cloud Run Job with Dynamic Env Vars..."
gcloud run jobs deploy ${JOB_NAME} \
    --image ${IMAGE_NAME} \
    --region ${REGION} \
    --tasks 1 \
    --max-retries 1 \
    --task-timeout 30m \
    --set-env-vars APIFY_API_TOKEN="${APIFY_API_TOKEN}",SERPER_API_KEY="${SERPER_API_KEY}",TAVILY_API_KEY="${TAVILY_API_KEY}",GOOGLE_DRIVE_FOLDER_ID="${GOOGLE_DRIVE_FOLDER_ID}",SENDER_EMAIL="${SENDER_EMAIL}",SENDER_APP_PASSWORD="${SENDER_APP_PASSWORD}",RECIPIENT_EMAIL="${RECIPIENT_EMAIL}",LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}",LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY}",LANGFUSE_HOST="${LANGFUSE_HOST}"

echo "✅ Deployment completed securely!"