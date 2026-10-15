#!/bin/sh
# Validates .env has all required production variables before docker compose up.
set -e

ENV_FILE="${1:-.env}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Error: $ENV_FILE not found."
  echo "Run: cp .env.example .env  &&  fill every value"
  exit 1
fi

read_var() {
  grep -E "^${1}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

required="
APTP_API_KEY
AZURE_OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_DEPLOYMENT
GOOGLE_DRIVE_CREDENTIALS_JSON
GOOGLE_DRIVE_SYSTEM_ROOT_FOLDER_ID
GOOGLE_DRIVE_WEBHOOK_SECRET
APTP_CORS_ORIGINS
"

for key in $required; do
  val=$(read_var "$key")
  if [ -z "$val" ]; then
    echo "Error: $key is empty in $ENV_FILE"
    exit 1
  fi
done

allow_mocks=$(read_var "APTP_ALLOW_MOCKS")
if [ "$allow_mocks" = "true" ] || [ "$allow_mocks" = "1" ]; then
  echo "Error: APTP_ALLOW_MOCKS must be false for production runs"
  exit 1
fi

mock_llm=$(read_var "APTP_USE_MOCK_LLM")
if [ "$mock_llm" != "false" ]; then
  echo "Error: APTP_USE_MOCK_LLM must be false in $ENV_FILE"
  exit 1
fi

mock_pw=$(read_var "APTP_MOCK_PLAYWRIGHT_EXECUTION")
if [ "$mock_pw" != "false" ]; then
  echo "Error: APTP_MOCK_PLAYWRIGHT_EXECUTION must be false in $ENV_FILE"
  exit 1
fi

skip_pw_install=$(read_var "APTP_SKIP_PLAYWRIGHT_INSTALL")
if [ "$skip_pw_install" = "true" ] || [ "$skip_pw_install" = "1" ]; then
  browsers_path=$(read_var "PLAYWRIGHT_BROWSERS_PATH")
  if [ -z "$browsers_path" ]; then
    echo "Warning: APTP_SKIP_PLAYWRIGHT_INSTALL=true but PLAYWRIGHT_BROWSERS_PATH is empty."
    echo "         Docker/Railway sets PLAYWRIGHT_BROWSERS_PATH=/ms-playwright in the image."
  fi
fi

embedding=$(read_var "AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
if [ -z "$embedding" ]; then
  echo "Error: AZURE_OPENAI_EMBEDDING_DEPLOYMENT is required in $ENV_FILE"
  exit 1
fi

echo "✓ $ENV_FILE validated (real-only: Azure LLM + embeddings + live Playwright + Drive)"
