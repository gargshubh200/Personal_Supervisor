cat << 'EOF' > deploy_gcp.sh
#!/bin/bash
set -e

# Configuration
PROJECT_ID="career-os-project"
REGION="asia-south1"
REPO_NAME="career-os-repo"
JOB_NAME="career-os-supervisor-job"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${JOB_NAME}:latest"

echo "1. Setting active project to ${PROJECT_ID}..."
gcloud config set project ${PROJECT_ID}

echo "2. Enabling GCP APIs..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudscheduler.googleapis.com \
    aiplatform.googleapis.com \
    firestore.googleapis.com \

echo "3. Ensuring Artifact Registry Repository exists..."
gcloud artifacts repositories create ${REPO_NAME} \
    --repository-format=docker \
    --location=${REGION} \
    --description="Career-OS Docker Repository" || true

echo "4. Submitting Docker build to Cloud Build..."
gcloud builds submit --tag ${IMAGE_NAME} .

echo "5. Deploying Cloud Run Job..."
gcloud run jobs deploy ${JOB_NAME} \
    --image ${IMAGE_NAME} \
    --region ${REGION} \
    --tasks 1 \
    --max-retries 1 \
    --task-timeout 30m \
    --set-env-vars APIFY_API_TOKEN="",SERPER_API_KEY="",TAVILY_API_KEY="",GOOGLE_DRIVE_FOLDER_ID="",LANGFUSE_SECRET_KEY="",LANGFUSE_PUBLIC_KEY="",LANGFUSE_BASE_URL="https://us.cloud.langfuse.com",SENDER_EMAIL="gargshubh200@gmail.com",SENDER_APP_PASSWORD="YOUR_16_CHAR_GMAIL_APP_PASSWORD",RECIPIENT_EMAIL="gargshubh200@gmail.com"

echo "6. Creating Daily Cloud Scheduler trigger..."
gcloud scheduler jobs create http "${JOB_NAME}-schedule" \
    --location ${REGION} \
    --schedule "0 8 * * *" \
    --time-zone "Asia/Kolkata" \
    --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
    --http-method POST \
    --oauth-service-account-email "${PROJECT_ID}@appspot.gserviceaccount.com" || echo "Scheduler trigger creation completed or job already exists."

echo "✅ Deployment pipeline completed successfully!"
EOF

chmod +x deploy_gcp.sh