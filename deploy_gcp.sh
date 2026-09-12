#!/bin/bash

# Configuration Variables
PROJECT_ID="career-os-project "
REGION="us-central1" # Bengaluru / Mumbai region
JOB_NAME="career-os-supervisor-job"
IMAGE_NAME="gcr.io/${PROJECT_ID}/${JOB_NAME}:latest"

# 1. Set Active GCP Project
gcloud config set project ${PROJECT_ID}

# 2. Enable Required GCP APIs
gcloud services enable \
    run.googleapis.com \
    containerregistry.googleapis.com \
    cloudscheduler.googleapis.com \
    secretmanager.googleapis.com

# 3. Build Container Image via GCP Cloud Build
gcloud builds submit --tag ${IMAGE_NAME} .

# 4. Create or Update GCP Cloud Run Job
gcloud run jobs deploy ${JOB_NAME} \
    --image ${IMAGE_NAME} \
    --region ${REGION} \
    --tasks 1 \
    --max-retries 1 \
    --task-timeout 30m \
    --set-env-vars APIFY_API_TOKEN="apify_api_JR6ldsdgqyFlf9S4B9u4YZ24pK5jVX3ApxRh",SERPER_API_KEY="9b264a6afd9e6b8166f5d952f0b2c771d44d67d7",TAVILY_API_KEY="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",GOOGLE_DRIVE_FOLDER_ID="1u81g2UkCzlDUQkbqpupoKgEI-hXpLUKD"

# 5. Schedule Daily Execution via GCP Cloud Scheduler (8:00 AM IST daily)
gcloud scheduler jobs create http ${JOB_NAME}-schedule \
    --location ${REGION} \
    --schedule "0 8 * * *" \
    --time-zone "Asia/Kolkata" \
    --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
    --http-method POST \
    --oauth-service-account-email "${PROJECT_ID}@appspot.gserviceaccount.com" || true