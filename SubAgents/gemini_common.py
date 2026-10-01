"""
Shared Vertex AI Gemini client + retry policy for every SubAgent.
Project id / model are overridable via env (GCP_PROJECT_ID, GEMINI_MODEL).
"""
import os

from dotenv import load_dotenv
from google import genai
from google.genai.errors import ClientError, ServerError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

load_dotenv()

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "career-os-project")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

client = genai.Client(
    vertexai=True,
    project=GCP_PROJECT_ID,
    location="global"
)

# 408/429 (timeout / rate limit) and 5xx are transient; other 4xx (bad request,
# permission, not found) will fail identically on every retry.
RETRYABLE_CLIENT_ERROR_CODES = {408, 429}


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    if isinstance(exc, ServerError):
        return True
    if isinstance(exc, ClientError):
        return getattr(exc, "code", None) in RETRYABLE_CLIENT_ERROR_CODES
    return False


gemini_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=10, max=60),
    retry=retry_if_exception(_is_retryable_gemini_error),
    reraise=True
)
